#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import pytest as pytest
from pytest_operator.plugin import OpsTest

from tests.integration.ha_tests.conftest import APPLICATION_NAME
from tests.integration.ha_tests.helpers import (
    are_writes_increasing,
    check_writes,
    start_continuous_writes,
)
from tests.integration.helpers import (
    DATABASE_APP_NAME,
    app_name,
    build_and_deploy,
    get_primary,
)

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest) -> None:
    """Build and deploy three unit of PostgreSQL."""
    wait_for_apps = False
    # Check if there is a pre-existing cluster.
    if not await app_name(ops_test):
        wait_for_apps = True
        await build_and_deploy(ops_test, 3, wait_for_idle=False)
    # Deploy the continuous writes application charm if it wasn't already deployed.
    if not await app_name(ops_test, APPLICATION_NAME):
        wait_for_apps = True
        async with ops_test.fast_forward():
            charm = await ops_test.build_charm("tests/integration/ha_tests/application-charm")
            await ops_test.model.deploy(charm, application_name=APPLICATION_NAME)

    if wait_for_apps:
        async with ops_test.fast_forward():
            await ops_test.model.wait_for_idle(status="active", timeout=1000)


async def test_upgrade(ops_test: OpsTest, continuous_writes) -> None:
    # Start an application that continuously writes data to the database.
    app = await app_name(ops_test)
    await start_continuous_writes(ops_test, app)

    # Check whether writes are increasing.
    primary_name = await get_primary(ops_test, app)
    await are_writes_increasing(ops_test, primary_name)

    # Run the pre-upgrade-check action.
    leader_unit_name = None
    for unit in ops_test.model.applications[app].units:
        if await unit.is_leader_from_status():
            leader_unit_name = unit.name
            break
    action = await ops_test.model.units.get(leader_unit_name).run_action("pre-upgrade-check")
    await action.wait()
    assert action.results["Code"] == "0"

    # Run juju refresh.
    application = ops_test.model.applications[app]
    charm = await ops_test.build_charm(".")
    await application.refresh(path=charm)
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active")

    # Check whether writes are increasing.
    primary_name = await get_primary(ops_test, app)
    await are_writes_increasing(ops_test, primary_name)

    # Verify that no writes to the database were missed after stopping the writes
    # (check that all the units have all the writes).
    await check_writes(ops_test)
