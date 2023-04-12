#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio
import logging
from time import sleep

import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from tests.integration.ha_tests.conftest import APPLICATION_NAME
from tests.integration.ha_tests.helpers import (
    METADATA,
    all_db_processes_down,
    check_cluster_is_updated,
    check_writes,
    check_writes_are_increasing,
    fetch_cluster_members,
    get_primary,
    modify_pebble_restart_delay,
    postgresql_ready,
    send_signal_to_process,
    start_continuous_writes,
)
from tests.integration.helpers import (
    CHARM_SERIES,
    app_name,
    build_and_deploy,
    get_unit_address,
)

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


@pytest.mark.parametrize("process", [POSTGRESQL_PROCESS])
async def test_kill_db_process(
    ops_test: OpsTest, process: str, continuous_writes, primary_start_timeout
) -> None:
    # Locate primary unit.
    app = await app_name(ops_test)
    primary_name = await get_primary(ops_test, app)

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # Kill the database process.
    await send_signal_to_process(ops_test, primary_name, process, "SIGKILL")

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


@pytest.mark.parametrize("process", DB_PROCESSES)
@pytest.mark.parametrize("signal", ["SIGTERM", "SIGKILL"])
async def test_full_cluster_restart(
    ops_test: OpsTest, process: str, signal: str, continuous_writes, restart_policy
) -> None:
    """This tests checks that a cluster recovers from a full cluster restart.

    The test can be called a full cluster crash when the signal sent to the OS process
    is SIGKILL.
    """
    # Locate primary unit.
    # Start an application that continuously writes data to the database.
    app = await app_name(ops_test)
    await start_continuous_writes(ops_test, app)

    # Restart all units "simultaneously".
    await asyncio.gather(
        *[
            send_signal_to_process(ops_test, unit.name, process, signal)
            for unit in ops_test.model.applications[app].units
        ]
    )

    # This test serves to verify behavior when all replicas are down at the same time that when
    # they come back online they operate as expected. This check verifies that we meet the criteria
    # of all replicas being down at the same time.
    assert await all_db_processes_down(ops_test, process), "Not all units down at the same time."
    if process == PATRONI_PROCESS:
        for unit in ops_test.model.applications[app].units:
            modify_pebble_restart_delay(
                ops_test,
                unit.name,
                "tests/integration/ha_tests/manifests/restore_pebble_restart_delay.yml",
            )

    # Verify all units are up and running.
    for unit in ops_test.model.applications[app].units:
        assert await postgresql_ready(
            ops_test, unit.name
        ), f"unit {unit.name} not restarted after cluster restart."

    await check_writes_are_increasing(ops_test)

    # Verify that all units are part of the same cluster.
    member_ips = await fetch_cluster_members(ops_test)
    ip_addresses = [
        await get_unit_address(ops_test, unit.name)
        for unit in ops_test.model.applications[app].units
    ]
    assert set(member_ips) == set(ip_addresses), "not all units are part of the same cluster."

    # Verify that no writes to the database were missed after stopping the writes.
    await check_writes(ops_test)
