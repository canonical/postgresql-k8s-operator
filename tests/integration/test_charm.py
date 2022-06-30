#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import os

import psycopg2
import pytest
import requests
from lightkube import AsyncClient
from lightkube.resources.core_v1 import Pod
from pytest_operator.plugin import OpsTest
from tenacity import retry, retry_if_result, stop_after_attempt, wait_exponential

from tests.helpers import METADATA, STORAGE_PATH
from tests.integration.helpers import (
    convert_records_to_dict,
    get_application_units,
    get_cluster_members,
    get_existing_patroni_k8s_resources,
    get_expected_patroni_k8s_resources,
    get_model_name,
    get_unit_address,
    scale_application,
)

logger = logging.getLogger(__name__)

APP_NAME = METADATA["name"]
UNIT_IDS = [0, 1, 2]


@pytest.mark.skipif(
    os.environ.get("PYTEST_SKIP_DEPLOY", False),
    reason="skipping deploy, model expected to be provided.",
)
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    """Build the charm-under-test and deploy it.

    Assert on the unit status before any relations/configurations take place.
    """
    # Build and deploy charm from local source folder.
    charm = await ops_test.build_charm(".")
    resources = {"postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"]}
    await ops_test.model.deploy(
        charm, resources=resources, application_name=APP_NAME, num_units=len(UNIT_IDS), trust=True
    )
    # Change update status hook interval to be triggered more often
    # (it's required to handle https://github.com/canonical/postgresql-k8s-operator/issues/3).
    await ops_test.model.set_config({"update-status-hook-interval": "5s"})

    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)
    for unit_id in UNIT_IDS:
        assert ops_test.model.applications[APP_NAME].units[unit_id].workload_status == "active"


@pytest.mark.abort_on_fail
async def test_application_created_required_resources(ops_test: OpsTest) -> None:
    # Compare the k8s resources that the charm and Patroni should create with
    # the currently created k8s resources.
    namespace = await get_model_name(ops_test)
    existing_resources = get_existing_patroni_k8s_resources(namespace, APP_NAME)
    expected_resources = get_expected_patroni_k8s_resources(namespace, APP_NAME)
    assert set(existing_resources) == set(expected_resources)


@pytest.mark.parametrize("unit_id", UNIT_IDS)
async def test_labels_consistency_across_pods(ops_test: OpsTest, unit_id: int) -> None:
    model = await ops_test.model.get_info()
    client = AsyncClient(namespace=model.name)
    pod = await client.get(Pod, name=f"postgresql-k8s-{unit_id}")
    # Ensures that the correct kubernetes labels are set
    # (these ones guarantee the correct working of replication).
    assert pod.metadata.labels["application"] == "patroni"
    assert pod.metadata.labels["cluster-name"] == model.name


@pytest.mark.parametrize("unit_id", UNIT_IDS)
async def test_database_is_up(ops_test: OpsTest, unit_id: int):
    # Query Patroni REST API and check the status that indicates
    # both Patroni and PostgreSQL are up and running.
    host = await get_unit_address(ops_test, APP_NAME, f"{APP_NAME}/{unit_id}")
    result = requests.get(f"http://{host}:8008/health")
    assert result.status_code == 200


@pytest.mark.parametrize("unit_id", UNIT_IDS)
async def test_settings_are_correct(ops_test: OpsTest, unit_id: int):
    password = await get_postgres_password(ops_test)

    # Connect to PostgreSQL.
    host = await get_unit_address(ops_test, APP_NAME, f"{APP_NAME}/{unit_id}")
    logger.info("connecting to the database host: %s", host)
    with psycopg2.connect(
        f"dbname='postgres' user='postgres' host='{host}' password='{password}' connect_timeout=1"
    ) as connection, connection.cursor() as cursor:
        assert connection.status == psycopg2.extensions.STATUS_READY

        # Retrieve settings from PostgreSQL pg_settings table.
        # Here the SQL query gets a key-value pair composed by the name of the setting
        # and its value, filtering the retrieved data to return only the settings
        # that were set by Patroni.
        cursor.execute(
            """SELECT name,setting
                FROM pg_settings
                WHERE name IN
                ('data_directory', 'cluster_name', 'data_checksums', 'listen_addresses');"""
        )
        records = cursor.fetchall()
        settings = convert_records_to_dict(records)

    # Validate each configuration set by Patroni on PostgreSQL.
    assert settings["cluster_name"] == (await ops_test.model.get_info()).name
    assert settings["data_directory"] == f"{STORAGE_PATH}/pgdata"
    assert settings["data_checksums"] == "on"
    assert settings["listen_addresses"] == "0.0.0.0"

    # Retrieve settings from Patroni REST API.
    result = requests.get(f"http://{host}:8008/config")
    settings = result.json()

    # Validate configuration exposed by Patroni.
    assert settings["postgresql"]["use_pg_rewind"]


async def test_cluster_is_stable_after_leader_deletion(ops_test: OpsTest) -> None:
    """Tests that the cluster maintains a primary after the primary is deleted."""
    # Find the current primary unit.
    primary = await get_primary(ops_test)

    # Delete the primary pod.
    model = await ops_test.model.get_info()
    client = AsyncClient(namespace=model.name)
    await client.delete(Pod, name=primary.replace("/", "-"))
    logger.info(f"deleted pod {primary}")

    # Wait and get the primary again (which can be any unit, including the previous primary).
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME], status="active", timeout=1000, wait_for_exact_units=3
    )
    primary = await get_primary(ops_test)

    # We also need to check that a replica can see the leader
    # to make sure that the cluster is stable again.
    other_unit_id = 1 if primary.split("/")[1] == 0 else 0
    assert await get_primary(ops_test, other_unit_id) != "None"


async def test_scale_down_and_up(ops_test: OpsTest):
    """Test data is replicated to new units after a scale up."""
    # Ensure the initial number of units in the application.
    initial_scale = len(UNIT_IDS)
    await scale_application(ops_test, APP_NAME, initial_scale)

    # Scale down the application.
    await scale_application(ops_test, APP_NAME, initial_scale - 1)

    # Ensure the member was correctly removed from the cluster
    # (by comparing the cluster members and the current units).
    primary = await get_primary(ops_test)
    address = await get_unit_address(ops_test, APP_NAME, primary)
    assert get_cluster_members(address) == get_application_units(ops_test, APP_NAME)

    # Scale up the application (2 more units than the current scale).
    await scale_application(ops_test, APP_NAME, initial_scale + 1)

    # Ensure the new members were added to the cluster.
    assert get_cluster_members(address) == get_application_units(ops_test, APP_NAME)

    # Scale the application to the initial scale.
    await scale_application(ops_test, APP_NAME, initial_scale)


async def test_persist_data_through_graceful_restart(ops_test: OpsTest):
    """Test data persists through a graceful restart."""
    primary = await get_primary(ops_test)
    password = await get_postgres_password(ops_test)
    address = await get_unit_address(ops_test, APP_NAME, primary)

    # Write data to primary IP.
    logger.info(f"connecting to primary {primary} on {address}")
    with db_connect(host=address, password=password) as connection:
        connection.autocommit = True
        connection.cursor().execute("CREATE TABLE gracetest (testcol INT );")

    # Restart all nodes by scaling to 0, then back up
    # These have to run sequentially for the test to be valid/stable.
    await ops_test.model.applications[APP_NAME].scale(0)
    await ops_test.model.applications[APP_NAME].scale(3)
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    # Testing write occurred to every postgres instance by reading from them
    status = await ops_test.model.get_status()  # noqa: F821
    for unit in status["applications"][APP_NAME]["units"].values():
        host = unit["address"]
        logger.info("connecting to the database host: %s", host)
        with db_connect(host=host, password=password) as connection:
            # Ensure we can read from "gracetest" table
            connection.cursor().execute("SELECT * FROM gracetest;")


async def test_persist_data_through_failure(ops_test: OpsTest):
    """Test data persists through a failure."""
    primary = await get_primary(ops_test)
    password = await get_postgres_password(ops_test)
    address = await get_unit_address(ops_test, APP_NAME, primary)

    # Write data to primary IP.
    logger.info(f"connecting to primary {primary} on {address}")
    with db_connect(host=address, password=password) as connection:
        connection.autocommit = True
        connection.cursor().execute("CREATE TABLE failtest (testcol INT );")

    # Cause a machine failure by killing a unit in k8s
    model = await ops_test.model.get_info()
    client = AsyncClient(namespace=model.name)
    await client.delete(Pod, name=primary.replace("/", "-"))
    logger.info("primary pod deleted")

    # Wait for juju to notice one of the pods is gone and fix it
    logger.info("wait for juju to reset postgres container")
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME],
        status="active",
        timeout=1000,
        wait_for_exact_units=3,
        check_freq=2,
        idle_period=45,
    )
    logger.info("juju has reset postgres container")

    # Testing write occurred to every postgres instance by reading from them
    status = await ops_test.model.get_status()  # noqa: F821
    for unit in status["applications"][APP_NAME]["units"].values():
        host = unit["address"]
        logger.info("connecting to the database host: %s", host)
        with db_connect(host=host, password=password) as connection:
            # Ensure we can read from "failtest" table
            connection.cursor().execute("SELECT * FROM failtest;")


async def test_automatic_failover_after_leader_issue(ops_test: OpsTest) -> None:
    """Tests that an automatic failover is triggered after an issue happens in the leader."""
    # Find the current primary unit.
    primary = await get_primary(ops_test)

    # Crash PostgreSQL by removing the data directory.
    await ops_test.model.units.get(primary).run(f"rm -rf {STORAGE_PATH}/pgdata")

    # Wait for charm to stabilise
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME], status="active", timeout=1000, wait_for_exact_units=3
    )

    # Primary doesn't have to be different, but it does have to exist.
    assert await get_primary(ops_test) != "None"


@retry(
    retry=retry_if_result(lambda x: not x),
    stop=stop_after_attempt(10),
    wait=wait_exponential(multiplier=1, min=2, max=30),
)
async def primary_changed(ops_test: OpsTest, old_primary: str) -> bool:
    """Checks whether or not the primary unit has changed."""
    primary = await get_primary(ops_test)
    return primary != old_primary


async def get_primary(ops_test: OpsTest, unit_id=0) -> str:
    """Get the primary unit.

    Args:
        ops_test: ops_test instance.
        unit_id: the number of the unit.

    Returns:
        the current primary unit.
    """
    action = await ops_test.model.units.get(f"{APP_NAME}/{unit_id}").run_action("get-primary")
    action = await action.wait()
    return action.results["primary"]


async def get_postgres_password(ops_test: OpsTest):
    """Retrieve the postgres user password using the action."""
    unit = ops_test.model.units.get(f"{APP_NAME}/0")
    action = await unit.run_action("get-postgres-password")
    result = await action.wait()
    return result.results["postgres-password"]


def db_connect(host: str, password: str):
    """Returns psycopg2 connection object linked to postgres db in the given host.

    Args:
        host: the IP of the postgres host container
        password: postgres password

    Returns:
        psycopg2 connection object linked to postgres db, under "postgres" user.
    """
    return psycopg2.connect(
        f"dbname='postgres' user='postgres' host='{host}' password='{password}' connect_timeout=10"
    )


@pytest.mark.abort_on_fail
async def test_application_removal_cleanup_resources(ops_test: OpsTest) -> None:
    # Remove the application and wait until it's gone.
    await ops_test.model.applications[APP_NAME].remove()
    await ops_test.model.block_until(lambda: APP_NAME not in ops_test.model.applications)

    # Check that all k8s resources created by the charm and Patroni were removed.
    namespace = await get_model_name(ops_test)
    existing_resources = get_existing_patroni_k8s_resources(namespace, APP_NAME)
    assert set(existing_resources) == set()
