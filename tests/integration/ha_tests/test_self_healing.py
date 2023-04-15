#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
from time import sleep

import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from tests.integration.ha_tests.conftest import APPLICATION_NAME
from tests.integration.ha_tests.helpers import (
    METADATA,
    check_cluster_is_updated,
    check_member_is_isolated,
    check_writes_are_increasing,
    get_primary,
    is_connection_possible,
    isolate_instance_from_cluster,
    postgresql_ready,
    remove_instance_isolation,
    send_signal_to_process,
    start_continuous_writes,
)
from tests.integration.helpers import CHARM_SERIES, app_name, build_and_deploy

logger = logging.getLogger(__name__)

APP_NAME = METADATA["name"]
PATRONI_PROCESS = "patroni"
POSTGRESQL_PROCESS = "postgres"
DB_PROCESSES = [POSTGRESQL_PROCESS, PATRONI_PROCESS]
MEDIAN_ELECTION_TIME = 10


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest) -> None:
    """Build and deploy three unit of PostgreSQL."""
    wait_for_apps = False
    # It is possible for users to provide their own cluster for HA testing. Hence, check if there
    # is a pre-existing cluster.
    if not await app_name(ops_test):
        wait_for_apps = True
        await build_and_deploy(ops_test, 3, wait_for_idle=False)
    # Deploy the continuous writes application charm if it wasn't already deployed.
    if not await app_name(ops_test, APPLICATION_NAME):
        wait_for_apps = True
        async with ops_test.fast_forward():
            charm = await ops_test.build_charm("tests/integration/ha_tests/application-charm")
            await ops_test.model.deploy(
                charm, application_name=APPLICATION_NAME, series=CHARM_SERIES
            )

    if wait_for_apps:
        async with ops_test.fast_forward():
            await ops_test.model.wait_for_idle(status="active", timeout=1000)


@pytest.mark.parametrize("process", [PATRONI_PROCESS])
async def test_freeze_db_process(
    ops_test: OpsTest, process: str, continuous_writes, primary_start_timeout
) -> None:
    # Locate primary unit.
    app = await app_name(ops_test)
    primary_name = await get_primary(ops_test, app)

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # Freeze the database process.
    await send_signal_to_process(ops_test, primary_name, process, "SIGSTOP")

    # Wait some time to elect a new primary.
    sleep(MEDIAN_ELECTION_TIME * 2)

    async with ops_test.fast_forward():
        try:
            await check_writes_are_increasing(ops_test, primary_name)

            # Verify that a new primary gets elected (ie old primary is secondary).
            for attempt in Retrying(stop=stop_after_delay(60 * 3), wait=wait_fixed(3)):
                with attempt:
                    new_primary_name = await get_primary(ops_test, app, down_unit=primary_name)
                    assert new_primary_name != primary_name
        finally:
            # Un-freeze the old primary.
            for attempt in Retrying(stop=stop_after_delay(60 * 3), wait=wait_fixed(3)):
                with attempt:
                    use_ssh = (attempt.retry_state.attempt_number % 2) == 0
                    logger.info(f"unfreezing {process}")
                    await send_signal_to_process(
                        ops_test, primary_name, process, "SIGCONT", use_ssh
                    )

        # Verify that the database service got restarted and is ready in the old primary.
        assert await postgresql_ready(ops_test, primary_name)

    await check_cluster_is_updated(ops_test, primary_name)


@pytest.mark.parametrize("process", DB_PROCESSES)
async def test_restart_db_process(
    ops_test: OpsTest, process: str, continuous_writes, primary_start_timeout
) -> None:
    # Locate primary unit.
    app = await app_name(ops_test)
    primary_name = await get_primary(ops_test, app)

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # Restart the database process.
    await send_signal_to_process(ops_test, primary_name, process, "SIGTERM")

    # Wait some time to elect a new primary.
    sleep(MEDIAN_ELECTION_TIME * 2)

    async with ops_test.fast_forward():
        await check_writes_are_increasing(ops_test, primary_name)

        # Verify that the database service got restarted and is ready in the old primary.
        assert await postgresql_ready(ops_test, primary_name)

    # Verify that a new primary gets elected (ie old primary is secondary).
    new_primary_name = await get_primary(ops_test, app, down_unit=primary_name)
    assert new_primary_name != primary_name

    await check_cluster_is_updated(ops_test, primary_name)


async def test_network_cut(
    ops_test: OpsTest, continuous_writes, primary_start_timeout, chaos_mesh
) -> None:
    """Completely cut and restore network."""
    # Locate primary unit.
    app = await app_name(ops_test)
    primary_name = await get_primary(ops_test, app)

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # Verify that connection is possible.
    logger.info("checking whether the connectivity to the database is working")
    assert await is_connection_possible(
        ops_test, primary_name
    ), f"Connection {primary_name} is not possible"

    # Confirm that the primary is not isolated from the cluster.
    logger.info("confirming that the primary is not isolated from the cluster")
    assert not await check_member_is_isolated(ops_test, primary_name, primary_name)

    # Create network chaos policy to isolate instance from cluster
    logger.info(f"Cutting network for {primary_name}")
    isolate_instance_from_cluster(ops_test, primary_name)

    # Verify that connection is not possible.
    logger.info("checking whether the connectivity to the database is not working")
    assert not await is_connection_possible(
        ops_test, primary_name
    ), "Connection is possible after network cut"

    logger.info("checking whether writes are increasing")
    await check_writes_are_increasing(ops_test, primary_name)

    logger.info("checking whether a new primary was elected")
    async with ops_test.fast_forward():
        # Verify that a new primary gets elected (ie old primary is secondary).
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
            with attempt:
                new_primary_name = await get_primary(ops_test, app, down_unit=primary_name)
                assert new_primary_name != primary_name

    # Confirm that the former primary is isolated from the cluster.
    logger.info("confirming that the former primary is isolated from the cluster")
    assert await check_member_is_isolated(ops_test, new_primary_name, primary_name)

    # Remove network chaos policy isolating instance from cluster.
    logger.info(f"Restoring network for {primary_name}")
    remove_instance_isolation(ops_test)

    # Verify that the database service got restarted and is ready in the old primary.
    logger.info("waiting for the database service to restart")
    assert await postgresql_ready(ops_test, primary_name)

    # Verify that connection is possible.
    logger.info("checking whether the connectivity to the database is working")
    assert await is_connection_possible(
        ops_test, primary_name
    ), "Connection is not possible after network restore"

    await check_cluster_is_updated(ops_test, primary_name)
