#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import pytest as pytest
from pytest_operator.plugin import OpsTest

from tests.integration.ha_tests.helpers import (
    app_name,
    change_master_start_timeout,
    get_master_start_timeout,
    stop_continuous_writes,
)
from tests.integration.helpers import CHARM_SERIES

APPLICATION_NAME = "application"


@pytest.fixture()
async def continuous_writes(ops_test: OpsTest) -> None:
    """Deploy the charm that makes continuous writes to PostgreSQL."""
    # Deploy the continuous writes application charm if it wasn't already deployed.
    async with ops_test.fast_forward():
        if await app_name(ops_test, APPLICATION_NAME) is None:
            charm = await ops_test.build_charm("tests/integration/ha_tests/application-charm")
            await ops_test.model.deploy(
                charm, application_name=APPLICATION_NAME, series=CHARM_SERIES
            )
            await ops_test.model.wait_for_idle(status="active", timeout=1000)

    # Start the continuous writes process by relating the application to the database or
    # by calling the action if the relation already exists.
    database_app = await app_name(ops_test)
    relations = [
        relation
        for relation in ops_test.model.applications[database_app].relations
        if not relation.is_peer
        and f"{relation.requires.application_name}:{relation.requires.name}"
        == "application:database"
    ]
    if not relations:
        await ops_test.model.relate(database_app, APPLICATION_NAME)
        await ops_test.model.wait_for_idle(status="active", timeout=1000)
    else:
        action = await ops_test.model.units.get(f"{APPLICATION_NAME}/0").run_action(
            "start-continuous-writes"
        )
        await action.wait()
    yield
    # Stop the continuous writes process and clear the written data at the end.
    await stop_continuous_writes(ops_test)
    action = await ops_test.model.units.get(f"{APPLICATION_NAME}/0").run_action(
        "clear-continuous-writes"
    )
    await action.wait()


@pytest.fixture()
async def master_start_timeout(ops_test: OpsTest) -> None:
    """Temporary change the master start timeout configuration."""
    # Change the parameter that makes the primary reelection faster.
    initial_master_start_timeout = await get_master_start_timeout(ops_test)
    await change_master_start_timeout(ops_test, 0)
    yield
    # Rollback to the initial configuration.
    await change_master_start_timeout(ops_test, initial_master_start_timeout)
