#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
from time import sleep

import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from tests.integration.ha_tests.conftest import APPLICATION_NAME
from tests.integration.ha_tests.helpers import (
    METADATA,
    check_writes,
    fetch_cluster_members,
    secondary_up_to_date,
    start_continuous_writes,
)
from tests.integration.helpers import (
    CHARM_SERIES,
    app_name,
    build_and_deploy,
    get_primary,
    get_unit_address,
    scale_application,
)

APP_NAME = METADATA["name"]
PATRONI_PROCESS = "/usr/local/bin/patroni"
POSTGRESQL_PROCESS = "postgres"
DB_PROCESSES = [POSTGRESQL_PROCESS, PATRONI_PROCESS]


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


async def test_reelection(ops_test: OpsTest, continuous_writes) -> None:
    """Kill primary unit, check reelection."""
    app = await app_name(ops_test)
    if len(ops_test.model.applications[app].units) < 2:
        await scale_application(ops_test, app, 2)

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    primary_name = await get_primary(ops_test)
    await ops_test.model.applications[app].remove_unit(primary_name)

    # Verify that a new primary gets elected (ie old primary is secondary).
    for attempt in Retrying(stop=stop_after_delay(60 * 3), wait=wait_fixed(3)):
        with attempt:
            new_primary_name = await get_primary(ops_test, app, down_unit=primary_name)
            assert new_primary_name != primary_name, "primary reelection haven't happened"

    # Verify that all units are part of the same cluster.
    member_ips = await fetch_cluster_members(ops_test)
    ip_addresses = [
        await get_unit_address(ops_test, unit.name)
        for unit in ops_test.model.applications[app].units
    ]
    assert set(member_ips) == set(ip_addresses), "not all units are part of the same cluster."

    # Verify that no writes to the database were missed after stopping the writes.
    total_expected_writes = await check_writes(ops_test)

    # Verify that replica is up-to-date.
    for unit in ops_test.model.applications[app].units:
        if unit.name != new_primary_name:
            assert await secondary_up_to_date(
                ops_test, unit.name, total_expected_writes
            ), f"secondary {unit.name} not up to date with the cluster after reelection."


async def test_consistency(ops_test: OpsTest, continuous_writes) -> None:
    """Write to primary, read data from secondaries (check consistency)."""
    app = await app_name(ops_test)
    if len(ops_test.model.applications[app].units) < 3:
        await scale_application(ops_test, app, 3)

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # Wait some time.
    sleep(5)

    # Verify that no writes to the database were missed.
    total_expected_writes = await check_writes(ops_test)

    # Verify that all the units are up-to-date.
    for unit in ops_test.model.applications[app].units:
        assert await secondary_up_to_date(
            ops_test, unit.name, total_expected_writes
        ), f"unit {unit.name} not up to date."


async def test_no_data_replicated_between_clusters(ops_test: OpsTest, continuous_writes) -> None:
    """Check that writes in one cluster not replicated to another cluster."""


async def test_preserve_data_on_delete(ops_test: OpsTest, continuous_writes) -> None:
    """Scale-up, read data from new member, scale down, check that member gone without data."""
