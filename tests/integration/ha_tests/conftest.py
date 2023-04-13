#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import pytest as pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from tests.integration.ha_tests.helpers import (
    change_primary_start_timeout,
    change_wal_settings,
    get_postgresql_parameter,
    get_primary_start_timeout,
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
async def primary_start_timeout(ops_test: OpsTest) -> None:
    """Temporary change the primary start timeout configuration."""
    # Change the parameter that makes the primary reelection faster.
    initial_primary_start_timeout = await get_primary_start_timeout(ops_test)
    await change_primary_start_timeout(ops_test, 0)
    yield
    # Rollback to the initial configuration.
    await change_primary_start_timeout(ops_test, initial_primary_start_timeout)


@pytest.fixture()
async def wal_settings(ops_test: OpsTest) -> None:
    """Restore the WAL settings to the initial values."""
    # Get the value for each setting.
    initial_max_wal_size = await get_postgresql_parameter(ops_test, "max_wal_size")
    initial_min_wal_size = await get_postgresql_parameter(ops_test, "min_wal_size")
    initial_wal_keep_segments = await get_postgresql_parameter(ops_test, "wal_keep_segments")
    yield
    # Rollback to the initial settings.
    app = await app_name(ops_test)
    for unit in ops_test.model.applications[app].units:
        await change_wal_settings(
            ops_test,
            unit.name,
            initial_max_wal_size,
            initial_min_wal_size,
            initial_wal_keep_segments,
        )
