#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import pytest as pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from tests.integration.ha_tests.helpers import (
    change_patroni_setting,
    deploy_chaos_mesh,
    destroy_chaos_mesh,
    get_patroni_setting,
    modify_pebble_restart_delay,
    remove_instance_isolation,
)
from tests.integration.helpers import app_name

APPLICATION_NAME = "application"


@pytest.fixture()
async def chaos_mesh(ops_test: OpsTest) -> None:
    """Deploys chaos mesh to the namespace and uninstalls it at the end."""
    deploy_chaos_mesh(ops_test.model.info.name)

    yield

    remove_instance_isolation(ops_test)
    destroy_chaos_mesh(ops_test.model.info.name)


@pytest.fixture()
async def continuous_writes(ops_test: OpsTest) -> None:
    """Deploy the charm that makes continuous writes to PostgreSQL."""
    yield
    # Clear the written data at the end.
    for attempt in Retrying(stop=stop_after_delay(60 * 5), wait=wait_fixed(3), reraise=True):
        with attempt:
            action = (
                await ops_test.model.applications[APPLICATION_NAME]
                .units[0]
                .run_action("clear-continuous-writes")
            )
            await action.wait()
            assert action.results["result"] == "True", "Unable to clear up continuous_writes table"


@pytest.fixture()
async def loop_wait(ops_test: OpsTest) -> None:
    """Temporary change the loop wait configuration."""
    # Change the parameter that makes Patroni wait for some more time before restarting PostgreSQL.
    initial_loop_wait = await get_patroni_setting(ops_test, "loop_wait")
    yield
    # Rollback to the initial configuration.
    await change_patroni_setting(ops_test, "loop_wait", initial_loop_wait)


@pytest.fixture(scope="module")
async def primary_start_timeout(ops_test: OpsTest) -> None:
    """Temporary change the primary start timeout configuration."""
    # Change the parameter that makes the primary reelection faster.
    initial_primary_start_timeout = await get_patroni_setting(ops_test, "primary_start_timeout")
    await change_patroni_setting(ops_test, "primary_start_timeout", 0)
    yield
    # Rollback to the initial configuration.
    await change_patroni_setting(ops_test, "primary_start_timeout", initial_primary_start_timeout)


@pytest.fixture()
async def restart_policy(ops_test: OpsTest) -> None:
    """Sets and resets service pebble restart policy on all units."""
    app = await app_name(ops_test)

    for unit in ops_test.model.applications[app].units:
        modify_pebble_restart_delay(
            ops_test,
            unit.name,
            "tests/integration/ha_tests/manifests/extend_pebble_restart_delay.yml",
        )

        async with ops_test.fast_forward():
            await ops_test.model.wait_for_idle(
                apps=[app],
                status="active",
                raise_on_blocked=True,
                timeout=5 * 60,
                idle_period=30,
            )

    yield
