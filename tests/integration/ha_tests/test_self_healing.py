#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
from time import sleep

import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from tests.integration.ha_tests.helpers import (
    METADATA,
    app_name,
    count_writes,
    cut_network_from_unit,
    get_primary,
    is_unit_reachable_from,
    postgresql_ready,
    restore_network_for_unit,
    secondary_up_to_date,
    start_continuous_writes,
    stop_continuous_writes,
)


@pytest.mark.abort_on_fail
@pytest.mark.ha_self_healing_tests
async def test_build_and_deploy(ops_test: OpsTest) -> None:
    """Build and deploy three unit of PostgreSQL."""
    # It is possible for users to provide their own cluster for HA testing. Hence, check if there
    # is a pre-existing cluster.
    if await app_name(ops_test):
        return

    charm = await ops_test.build_charm(".")
    async with ops_test.fast_forward():
        await ops_test.model.deploy(
            charm,
            resources={
                "postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"]
            },
            num_units=3,
            trust=True,
        )
        await ops_test.model.wait_for_idle(status="active", timeout=1000)


@pytest.mark.ha_self_healing_tests
async def test_network_cut(ops_test: OpsTest, continuous_writes, master_start_timeout):
    """Completely cut and restore network."""
    # Locate primary unit.
    app = await app_name(ops_test)
    primary_name = await get_primary(ops_test, app)

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # Cut the network from the primary unit.
    await cut_network_from_unit(ops_test, primary_name)

    sleep(10)

    async with ops_test.fast_forward():
        # Verify new writes are continuing by counting the number of writes before and after a
        # 2 minutes wait.
        writes = await count_writes(ops_test)
        for attempt in Retrying(stop=stop_after_delay(60 * 2), wait=wait_fixed(3)):
            with attempt:
                more_writes = await count_writes(ops_test)
                assert more_writes > writes, "writes not continuing to DB"

                # Verify that the old primary is not reachable from the other units.
                unit_names = [unit.name for unit in ops_test.model.applications[app].units]
                for unit_name in set(unit_names) - {primary_name}:
                    assert not await is_unit_reachable_from(
                        ops_test, unit_name, primary_name
                    ), "❌ unit is reachable from peer"

                # Verify that the old primary is not reachable from the controller.
                assert not await is_unit_reachable_from(
                    ops_test, "controller-0", primary_name, use_controller_namespace=True
                ), "❌ unit is reachable from controller"

                # verify that connection is not possible
                assert not await postgresql_ready(
                    ops_test, primary_name, timeout=60
                ), "❌ Connection is possible after network cut"

                # Restore the network connection in the old primary.
                await restore_network_for_unit(ops_test, primary_name)

                # Verify that the database service is ready in the old primary.
                assert await postgresql_ready(
                    ops_test, primary_name
                ), "❌ Connection is not possible after network restore"

    # Verify that a new primary gets elected (ie old primary is secondary).
    new_primary_name = await get_primary(ops_test, app)
    assert new_primary_name != primary_name

    # Verify that no writes to the database were missed after stopping the writes.
    total_expected_writes = await stop_continuous_writes(ops_test)
    for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
        with attempt:
            actual_writes = await count_writes(ops_test)
            assert total_expected_writes == actual_writes, "writes to the db were missed."

    # Verify that old primary is up-to-date.
    assert await secondary_up_to_date(
        ops_test, primary_name, total_expected_writes
    ), "secondary not up to date with the cluster after reconnecting."
