#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from typing import get_args

import psycopg2
import pytest
import requests
from lightkube import AsyncClient
from lightkube.resources.core_v1 import Pod
from psycopg2 import sql
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from locales import ROCK_LOCALES

from .ha_tests.helpers import get_cluster_roles
from .helpers import (
    CHARM_BASE,
    METADATA,
    STORAGE_PATH,
    build_and_deploy,
    convert_records_to_dict,
    db_connect,
    get_application_units,
    get_cluster_members,
    get_existing_k8s_resources,
    get_expected_k8s_resources,
    get_password,
    get_primary,
    get_unit_address,
    run_command_on_unit,
    scale_application,
)

logger = logging.getLogger(__name__)

APP_NAME = METADATA["name"]
UNIT_IDS = [0, 1, 2]


@pytest.mark.abort_on_fail
@pytest.mark.skip_if_deployed
async def test_build_and_deploy(ops_test: OpsTest, charm):
    """Build the charm-under-test and deploy it.

    Assert on the unit status before any relations/configurations take place.
    """
    async with ops_test.fast_forward():
        await build_and_deploy(ops_test, charm, len(UNIT_IDS), APP_NAME)
    for unit_id in UNIT_IDS:
        assert ops_test.model.applications[APP_NAME].units[unit_id].workload_status == "active"


async def test_application_created_required_resources(ops_test: OpsTest) -> None:
    # Compare the k8s resources that the charm and Patroni should create with
    # the currently created k8s resources.
    namespace = ops_test.model.info.name
    existing_resources = get_existing_k8s_resources(namespace, APP_NAME)
    expected_resources = get_expected_k8s_resources(APP_NAME)
    assert set(existing_resources) == set(expected_resources)


@pytest.mark.parametrize("unit_id", UNIT_IDS)
async def test_labels_consistency_across_pods(ops_test: OpsTest, unit_id: int) -> None:
    model = ops_test.model.info
    client = AsyncClient(namespace=model.name)
    pod = await client.get(Pod, name=f"postgresql-k8s-{unit_id}")
    # Ensures that the correct kubernetes labels are set
    # (these ones guarantee the correct working of replication).
    assert pod.metadata.labels["application"] == "patroni"
    assert pod.metadata.labels["cluster-name"] == f"patroni-{APP_NAME}"


@pytest.mark.parametrize("unit_id", UNIT_IDS)
async def test_database_is_up(ops_test: OpsTest, unit_id: int):
    # Query Patroni REST API and check the status that indicates
    # both Patroni and PostgreSQL are up and running.
    host = await get_unit_address(ops_test, f"{APP_NAME}/{unit_id}")
    result = requests.get(f"https://{host}:8008/health", verify=False)
    assert result.status_code == 200


@pytest.mark.parametrize("unit_id", UNIT_IDS)
async def test_exporter_is_up(ops_test: OpsTest, unit_id: int):
    # Query exporter metrics endpoint and check the status that indicates
    # metrics are available for scraping.
    host = await get_unit_address(ops_test, f"{APP_NAME}/{unit_id}")
    result = requests.get(f"http://{host}:9187/metrics")
    assert result.status_code == 200
    assert "pg_exporter_last_scrape_error 0" in result.content.decode("utf8"), (
        "Scrape error in postgresql_prometheus_exporter"
    )


@pytest.mark.parametrize("unit_id", UNIT_IDS)
async def test_settings_are_correct(ops_test: OpsTest, unit_id: int):
    password = await get_password(ops_test)

    # Connect to PostgreSQL.
    host = await get_unit_address(ops_test, f"{APP_NAME}/{unit_id}")
    logger.info("connecting to the database host: %s", host)
    with (
        psycopg2.connect(
            f"dbname='postgres' user='operator' host='{host}' password='{password}' connect_timeout=1"
        ) as connection,
        connection.cursor() as cursor,
    ):
        assert connection.status == psycopg2.extensions.STATUS_READY

        # Retrieve settings from PostgreSQL pg_settings table.
        # Here the SQL query gets a key-value pair composed by the name of the setting
        # and its value, filtering the retrieved data to return only the settings
        # that were set by Patroni.
        settings_names = [
            "archive_command",
            "archive_mode",
            "autovacuum",
            "data_directory",
            "cluster_name",
            "data_checksums",
            "fsync",
            "full_page_writes",
            "lc_messages",
            "listen_addresses",
            "log_autovacuum_min_duration",
            "log_checkpoints",
            "log_destination",
            "log_temp_files",
            "log_timezone",
            "max_connections",
            "wal_level",
        ]
        cursor.execute(
            sql.SQL("SELECT name,setting FROM pg_settings WHERE name IN ({});").format(
                sql.SQL(", ").join(sql.Placeholder() * len(settings_names))
            ),
            settings_names,
        )
        records = cursor.fetchall()
        settings = convert_records_to_dict(records)

    # Validate each configuration set by Patroni on PostgreSQL.
    assert settings["archive_command"] == "/bin/true"
    assert settings["archive_mode"] == "on"
    assert settings["autovacuum"] == "on"
    assert settings["cluster_name"] == f"patroni-{APP_NAME}"
    assert settings["data_directory"] == f"{STORAGE_PATH}/pgdata"
    assert settings["data_checksums"] == "on"
    assert settings["fsync"] == "on"
    assert settings["full_page_writes"] == "on"
    assert settings["lc_messages"] == "en_US.UTF8"
    assert settings["listen_addresses"] == "0.0.0.0"
    assert settings["log_autovacuum_min_duration"] == "60000"
    assert settings["log_checkpoints"] == "on"
    assert settings["log_destination"] == "stderr"
    assert settings["log_temp_files"] == "1"
    assert settings["log_timezone"] == "UTC"
    assert settings["max_connections"] == "100"
    assert settings["wal_level"] == "logical"

    # Retrieve settings from Patroni REST API.
    result = requests.get(f"https://{host}:8008/config", verify=False)
    settings = result.json()

    # Validate configuration exposed by Patroni.
    assert settings["postgresql"]["use_pg_rewind"] is True
    assert settings["postgresql"]["remove_data_directory_on_rewind_failure"] is False
    assert settings["postgresql"]["remove_data_directory_on_diverged_timelines"] is False


async def test_postgresql_locales(ops_test: OpsTest) -> None:
    raw_locales = await run_command_on_unit(
        ops_test,
        ops_test.model.applications[APP_NAME].units[0].name,
        "locale -a",
    )
    locales = raw_locales.splitlines()
    locales.sort()

    # Juju 2 has an extra empty element
    if "" in locales:
        locales.remove("")
    assert locales == list(get_args(ROCK_LOCALES))


async def test_postgresql_parameters_change(ops_test: OpsTest) -> None:
    """Test that's possible to change PostgreSQL parameters."""
    await ops_test.model.applications[APP_NAME].set_config({
        "memory_max_prepared_transactions": "100",
        "memory_shared_buffers": "32768",  # 2 * 128MB. Patroni may refuse the config if < 128MB
        "response_lc_monetary": "en_GB.utf8",
        "experimental_max_connections": "200",
    })
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", idle_period=30)
    password = await get_password(ops_test)

    # Connect to PostgreSQL.
    for unit_id in UNIT_IDS:
        host = await get_unit_address(ops_test, f"{APP_NAME}/{unit_id}")
        logger.info("connecting to the database host: %s", host)
        try:
            with (
                psycopg2.connect(
                    f"dbname='postgres' user='operator' host='{host}' password='{password}' connect_timeout=1"
                ) as connection,
                connection.cursor() as cursor,
            ):
                settings_names = [
                    "max_prepared_transactions",
                    "shared_buffers",
                    "lc_monetary",
                    "max_connections",
                ]
                cursor.execute(
                    sql.SQL("SELECT name,setting FROM pg_settings WHERE name IN ({});").format(
                        sql.SQL(", ").join(sql.Placeholder() * len(settings_names))
                    ),
                    settings_names,
                )
                records = cursor.fetchall()
                settings = convert_records_to_dict(records)

                # Validate each configuration set by Patroni on PostgreSQL.
                assert settings["max_prepared_transactions"] == "100"
                assert settings["shared_buffers"] == "32768"
                assert settings["lc_monetary"] == "en_GB.utf8"
                assert settings["max_connections"] == "200"
        finally:
            connection.close()


async def test_cluster_is_stable_after_leader_deletion(ops_test: OpsTest) -> None:
    """Tests that the cluster maintains a primary after the primary is deleted."""
    # Find the current primary unit.
    primary = await get_primary(ops_test)

    # Delete the primary pod.
    model = ops_test.model.info
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
    assert await get_primary(ops_test, down_unit=primary) != "None"


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
    address = await get_unit_address(ops_test, primary)
    assert get_cluster_members(address) == get_application_units(ops_test, APP_NAME)

    # Scale up the application (2 more units than the current scale).
    await scale_application(ops_test, APP_NAME, initial_scale + 1)

    # Ensure the new members were added to the cluster.
    assert get_cluster_members(address) == get_application_units(ops_test, APP_NAME)

    # Scale the application to the initial scale.
    await scale_application(ops_test, APP_NAME, initial_scale)


async def test_switchover_sync_standby(ops_test: OpsTest):
    original_roles = await get_cluster_roles(
        ops_test, ops_test.model.applications[APP_NAME].units[0].name
    )
    run_action = await ops_test.model.units[original_roles["sync_standbys"][0]].run_action(
        "promote-to-primary", scope="unit"
    )
    await run_action.wait()
    await ops_test.model.wait_for_idle(status="active", timeout=200)
    new_roles = await get_cluster_roles(
        ops_test, ops_test.model.applications[APP_NAME].units[0].name
    )
    assert new_roles["primaries"][0] == original_roles["sync_standbys"][0]


async def test_persist_data_through_graceful_restart(ops_test: OpsTest):
    """Test data persists through a graceful restart."""
    primary = await get_primary(ops_test)
    password = await get_password(ops_test)
    address = await get_unit_address(ops_test, primary)

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
    status = await ops_test.model.get_status()
    for unit in status["applications"][APP_NAME]["units"].values():
        host = unit["address"]
        logger.info("connecting to the database host: %s", host)
        with db_connect(host=host, password=password) as connection:
            # Ensure we can read from "gracetest" table
            connection.cursor().execute("SELECT * FROM gracetest;")


async def test_persist_data_through_failure(ops_test: OpsTest):
    """Test data persists through a failure."""
    primary = await get_primary(ops_test)
    password = await get_password(ops_test)
    address = await get_unit_address(ops_test, primary)

    # Write data to primary IP.
    logger.info(f"connecting to primary {primary} on {address}")
    with db_connect(host=address, password=password) as connection:
        connection.autocommit = True
        connection.cursor().execute("CREATE TABLE failtest (testcol INT );")

    # Cause a machine failure by killing a unit in k8s
    model = ops_test.model.info
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
    status = await ops_test.model.get_status()
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


async def test_application_removal(ops_test: OpsTest) -> None:
    # Remove the application to trigger some hooks (like peer relation departed).
    await ops_test.model.applications[APP_NAME].remove()

    # Block until the application is completely removed, or any unit gets in an error state.
    await ops_test.model.block_until(
        lambda: APP_NAME not in ops_test.model.applications
        or any(
            unit.workload_status == "error" for unit in ops_test.model.applications[APP_NAME].units
        )
    )

    # Check that all k8s resources created by the charm and Patroni were removed.
    namespace = ops_test.model.info.name
    for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
        with attempt:
            existing_resources = get_existing_k8s_resources(namespace, APP_NAME)
            logger.info(f"existing_resources: {existing_resources}")
            assert set(existing_resources) == set()

    # Check whether the application is gone
    # (in that situation, the units aren't in an error state).
    assert APP_NAME not in ops_test.model.applications


@pytest.mark.skip(reason="Unstable")
async def test_redeploy_charm_same_model(ops_test: OpsTest, charm):
    """Redeploy the charm in the same model to test that it works."""
    async with ops_test.fast_forward():
        await ops_test.model.deploy(
            charm,
            resources={
                "postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"]
            },
            application_name=APP_NAME,
            num_units=len(UNIT_IDS),
            base=CHARM_BASE,
            trust=True,
            config={"profile": "testing"},
        )

        # This check is enough to ensure that the charm/workload is working for this specific test.
        await ops_test.model.wait_for_idle(
            apps=[APP_NAME], status="active", timeout=1000, wait_for_exact_units=len(UNIT_IDS)
        )


@pytest.mark.skip(reason="Unstable")
async def test_redeploy_charm_same_model_after_forcing_removal(ops_test: OpsTest, charm) -> None:
    """Redeploy the charm in the same model to test that it works after a forceful removal."""
    return_code, _, stderr = await ops_test.juju(
        "remove-application", APP_NAME, "--destroy-storage", "--force", "--no-prompt", "--no-wait"
    )
    if return_code != 0:
        assert False, stderr

    # Block until the application is completely removed, or any unit gets in an error state.
    await ops_test.model.block_until(
        lambda: APP_NAME not in ops_test.model.applications
        or any(
            unit.workload_status == "error" for unit in ops_test.model.applications[APP_NAME].units
        )
    )

    # Check that all k8s resources created by the charm and Patroni were still present.
    namespace = ops_test.model.info.name
    existing_resources = get_existing_k8s_resources(namespace, APP_NAME)
    expected_resources = get_expected_k8s_resources(APP_NAME)
    assert set(existing_resources) == set(expected_resources)

    # Check that the charm can be deployed again.
    async with ops_test.fast_forward():
        await ops_test.model.deploy(
            charm,
            resources={
                "postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"]
            },
            application_name=APP_NAME,
            num_units=len(UNIT_IDS),
            base=CHARM_BASE,
            trust=True,
            config={"profile": "testing"},
        )

        # This check is enough to ensure that the charm/workload is working for this specific test.
        await ops_test.model.wait_for_idle(
            apps=[APP_NAME],
            status="active",
            timeout=1000,
            wait_for_exact_units=len(UNIT_IDS),
            raise_on_error=False,
        )


async def test_storage_with_more_restrictive_permissions(ops_test: OpsTest, charm):
    """Test that the charm can be deployed with a storage with more restrictive permissions."""
    app_name = f"test-storage-{APP_NAME}"
    async with ops_test.fast_forward():
        # Deploy and wait for the charm to get into the install hook (maintenance status).
        async with ops_test.fast_forward():
            await ops_test.model.deploy(
                charm,
                resources={
                    "postgresql-image": METADATA["resources"]["postgresql-image"][
                        "upstream-source"
                    ]
                },
                application_name=app_name,
                num_units=1,
                base=CHARM_BASE,
                trust=True,
                config={"profile": "testing"},
            )

        # Restrict the permissions of the storage.
        command = "chmod 755 /var/lib/postgresql/data"
        complete_command = f"ssh --container postgresql {app_name}/0 {command}"
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3), reraise=True):
            with attempt:
                return_code, _, _ = await ops_test.juju(*complete_command.split())
                if return_code != 0:
                    raise Exception(
                        "Expected command %s to succeed instead it failed: %s",
                        command,
                        return_code,
                    )

        # This check is enough to ensure that the charm/workload is working for this specific test.
        await ops_test.model.wait_for_idle(apps=[app_name], status="active", timeout=1000)
