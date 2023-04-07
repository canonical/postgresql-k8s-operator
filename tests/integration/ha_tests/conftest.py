#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
from asyncio import gather

import pytest as pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from tests.integration.ha_tests.helpers import (
    ORIGINAL_RESTART_CONDITION,
    RESTART_CONDITION,
    change_master_start_timeout,
    get_master_start_timeout,
    update_restart_condition,
)
from tests.integration.helpers import app_name

APPLICATION_NAME = "application"


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


@pytest.fixture(scope="module")
async def master_start_timeout(ops_test: OpsTest) -> None:
    """Temporary change the master start timeout configuration."""
    # Change the parameter that makes the primary reelection faster.
    initial_master_start_timeout = await get_master_start_timeout(ops_test)
    await change_master_start_timeout(ops_test, 0)
    yield
    # Rollback to the initial configuration.
    await change_master_start_timeout(ops_test, initial_master_start_timeout)


@pytest.fixture()
async def reset_restart_condition(ops_test: OpsTest):
    """Resets service file delay on all units."""
    app = await app_name(ops_test)

    awaits = []
    for unit in ops_test.model.applications[app].units:
        awaits.append(update_restart_condition(ops_test, unit, RESTART_CONDITION))
    await gather(*awaits)

    yield

    awaits = []
    for unit in ops_test.model.applications[app].units:
        awaits.append(update_restart_condition(ops_test, unit, ORIGINAL_RESTART_CONDITION))
    await gather(*awaits)
