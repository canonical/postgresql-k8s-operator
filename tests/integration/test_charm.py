#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
import os

import psycopg2
import pytest
from jinja2 import Template
from lightkube import AsyncClient
from lightkube.resources.core_v1 import Pod
from pytest_operator.plugin import OpsTest
from tenacity import retry, retry_if_result, stop_after_attempt, wait_exponential

from tests.helpers import METADATA, STORAGE_PATH

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

    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)
    assert ops_test.model.applications[APP_NAME].units[0].workload_status == "active"



@pytest.mark.parametrize("unit_id", UNIT_IDS)
async def test_labels_consistency_across_pods(ops_test: OpsTest, unit_id: int) -> None:
    model = await ops_test.model.get_info()
    client = AsyncClient(namespace=model.name)
    pod = await client.get(Pod, name=f"postgresql-k8s-{unit_id}")
    # Ensures that the correct kubernetes labels are set
    # (these ones guarantee the correct working of replication).
    assert pod.metadata.labels["application"] == "patroni"
    assert pod.metadata.labels["cluster-name"] == model.name



async def test_database_is_up(ops_test: OpsTest):
    # Retrieving the postgres user password using the action.
    action = await ops_test.model.units.get(f"{APP_NAME}/0").run_action("get-postgres-password")
    action = await action.wait()
    password = action.results["postgres-password"]

    # Testing the connection to each PostgreSQL instance.
    status = await ops_test.model.get_status()  # noqa: F821
    for unit in status["applications"][APP_NAME]["units"].values():
        host = unit["address"]
        logger.info("connecting to the database host: %s", host)
        connection = psycopg2.connect(
            f"dbname='postgres' user='postgres' host='{host}' password='{password}' connect_timeout=1"
        )
        assert connection.status == psycopg2.extensions.STATUS_READY
        connection.close()


@pytest.mark.skip
@pytest.mark.parametrize("unit_id", UNIT_IDS)
async def test_config_files_are_correct(ops_test: OpsTest, unit_id: int):
    unit_name = f"postgresql-k8s/{unit_id}"

    # Retrieve the pod IP.
    status = await ops_test.model.get_status()  # noqa: F821
    for _, unit in status["applications"][APP_NAME]["units"].items():
        if unit["provider-id"] == unit_name.replace("/", "-"):
            pod_ip = unit["address"]
            break

    # Get the expected contents from files.
    with open("templates/patroni.yml.j2") as file:
        template = Template(file.read())
    expected_patroni_yml = template.render(pod_ip=pod_ip, storage_path=STORAGE_PATH)
    with open("tests/data/postgresql.conf") as file:
        expected_postgresql_conf = file.read()

    unit = ops_test.model.units[unit_name]

    # Check whether Patroni configuration is correctly set up.
    patroni_yml_data = await pull_content_from_unit_file(unit, f"{STORAGE_PATH}/patroni.yml")
    assert patroni_yml_data == expected_patroni_yml

    # Check that the PostgreSQL settings are as expected.
    postgresql_conf_data = await pull_content_from_unit_file(
        unit, f"{STORAGE_PATH}/postgresql-k8s-operator.conf"
    )
    assert postgresql_conf_data == expected_postgresql_conf


@pytest.mark.skip
async def test_cluster_is_stable_after_leader_deletion(ops_test: OpsTest) -> None:
    """Tests that the cluster maintains a primary after the primary is deleted."""
    # Find the current primary unit.
    primary = await get_primary(ops_test)

    # Delete the primary pod.
    model = await ops_test.model.get_info()
    client = AsyncClient(namespace=model.name)
    await client.delete(Pod, name=primary.replace("/", "-"))

    # Wait and get the primary again (which can be any unit, including the previous primary).
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)
    primary = await get_primary(ops_test)

    # We also need to check that a replica can see the leader
    # to make sure that the cluster is stable again.
    other_unit_id = 1 if primary.split("/")[1] == 0 else 0
    assert await get_primary(ops_test, other_unit_id) != "None"

@pytest.mark.skip
async def test_persist_data_through_graceful_restart(ops_test: OpsTest):
    """Test data persists through a graceful restart."""
    primary_name = await get_primary(ops_test)
    primary = ops_test.model.units[primary_name]

    # Retrieving the postgres user password using the action.
    action = await primary.run_action("get-postgres-password")
    action = await action.wait()
    password = action.results["postgres-password"]

    status = await ops_test.model.get_status()
    address = status["applications"][APP_NAME].units[primary_name]["address"]

    # Write data to primary IP.
    logger.info(f"connecting to primary {primary_name} on {address}")
    connection = psycopg2.connect(
        f"dbname='postgres' user='postgres' host='{address}' password='{password}' connect_timeout=10"
    )
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE gracetest (testcol INT );")
    finally:
        if connection:
            connection.close()

    # Restart all nodes by scaling to 0, then back up
    await ops_test.model.applications[APP_NAME].scale(0)
    await ops_test.model.applications[APP_NAME].scale(3)
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    # Testing write occurred to every postgres instance by reading from them
    status = await ops_test.model.get_status()  # noqa: F821
    for unit in status["applications"][APP_NAME]["units"].values():
        host = unit["address"]
        logger.info("connecting to the database host: %s", host)
        connection = psycopg2.connect(
            f"dbname='postgres' user='postgres' host='{host}' password='{password}' connect_timeout=10"
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM gracetest;")
        connection.close()


async def test_persist_data_through_failure(ops_test: OpsTest):
    """Test data persists through a failure."""
    primary_name = await get_primary(ops_test)
    primary = ops_test.model.units[primary_name]

    # Retrieving the postgres user password using the action.
    action = await primary.run_action("get-postgres-password")
    action = await action.wait()
    password = action.results["postgres-password"]

    status = await ops_test.model.get_status()
    address = status["applications"][APP_NAME].units[primary_name]["address"]

    # Write data to primary IP.
    logger.info(f"connecting to primary {primary_name} on {address}")
    try:
        connection = psycopg2.connect(
            f"dbname='postgres' user='postgres' host='{address}' password='{password}' connect_timeout=10"
        )
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE failtest (testcol INT );")
    finally:
        if connection:
            connection.close()

    # Restart badly (kill unit in microk8s)
    model = await ops_test.model.get_info()
    client = AsyncClient(namespace=model.name)
    await client.delete(Pod, name=primary_name.replace("/", "-"))
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    # Testing write occurred to every postgres instance by reading from them
    status = await ops_test.model.get_status()  # noqa: F821
    for unit in status["applications"][APP_NAME]["units"].values():
        host = unit["address"]
        logger.info("connecting to the database host: %s", host)
        connection = psycopg2.connect(
            f"dbname='postgres' user='postgres' host='{host}' password='{password}' connect_timeout=10"
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM failtest;")
        connection.close()


@pytest.mark.skip
async def test_automatic_failover_after_leader_issue(ops_test: OpsTest) -> None:
    """Tests that an automatic failover is triggered after an issue happens in the leader."""
    # Change update status hook interval to be triggered more often
    # (it's required to handle https://github.com/canonical/postgresql-k8s-operator/issues/3).
    await ops_test.model.set_config({"update-status-hook-interval": "5s"})

    # Find the current primary unit.
    primary = await get_primary(ops_test)

    # Crash PostgreSQL by removing the data directory.
    await ops_test.model.units.get(primary).run(f"rm -rf {STORAGE_PATH}/pgdata")

    # Wait for charm to stabilise
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

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


@retry(
    retry=retry_if_result(lambda x: x == "None"),
    stop=stop_after_attempt(10),
    wait=wait_exponential(multiplier=1, min=2, max=30),
)
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


async def pull_content_from_unit_file(unit, path: str) -> str:
    """Pull the content of a file from one unit.

    Args:
        unit: the Juju unit instance.
        path: the path of the file to get the contents from.

    Returns:
        the entire content of the file.
    """
    action = await unit.run(f"cat {path}")
    return action.results.get("Stdout", None)
