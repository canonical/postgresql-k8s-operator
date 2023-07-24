#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import pytest as pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_attempt, wait_fixed

from tests.integration.ha_tests.conftest import APPLICATION_NAME
from tests.integration.ha_tests.helpers import (
    are_writes_increasing,
    check_writes,
    start_continuous_writes,
)
from tests.integration.helpers import (
    app_name,
    build_and_deploy,
    count_switchovers,
    get_primary,
    set_role_for_unit,
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


@pytest.mark.parametrize("first_unit_role", ["replica"])  # , "sync_standby", "replica"
async def test_upgrade(ops_test: OpsTest, first_unit_role) -> None:
    # Change the role of the first unit if needed for this test run.
    app = await app_name(ops_test)
    await set_role_for_unit(ops_test, f"{app}/0", first_unit_role)

    # Start an application that continuously writes data to the database.
    logger.info("starting continuous writes to the database")
    await start_continuous_writes(ops_test, app)

    # Check whether writes are increasing.
    logger.info("checking whether writes are increasing")
    primary_name = await get_primary(ops_test, app)
    await are_writes_increasing(ops_test, primary_name)

    initial_number_of_switchovers = await count_switchovers(ops_test, primary_name)

    # Run the pre-upgrade-check action.
    for attempt in Retrying(stop=stop_after_attempt(2), wait=wait_fixed(30), reraise=True):
        with attempt:
            logger.info("running pre-upgrade check")
            leader_unit_name = None
            for unit in ops_test.model.applications[app].units:
                if await unit.is_leader_from_status():
                    leader_unit_name = unit.name
                    break
            action = await ops_test.model.units.get(leader_unit_name).run_action(
                "pre-upgrade-check"
            )
            await action.wait()
            assert action.results["Code"] == "0"

            # Ensure the primary has changed to the first unit.
            primary_name = await get_primary(ops_test, app)
            assert primary_name == f"{app}/0"

    # Run juju refresh.
    logger.info("refreshing the charm")
    application = ops_test.model.applications[app]
    charm = await ops_test.build_charm(".")
    await application.refresh(path=charm)
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(apps=[app], status="active")

    # Run the resume-upgrade action.
    logger.info("resuming upgrade")
    for unit in ops_test.model.applications[app].units:
        if await unit.is_leader_from_status():
            leader_unit_name = unit.name
            break
    print(f"leader_unit_name: {leader_unit_name}")
    action = await ops_test.model.units.get(leader_unit_name).run_action("resume-upgrade")
    await action.wait()
    assert action.results["Code"] == "0"
    async with ops_test.fast_forward(fast_interval="60s"):
        await ops_test.model.wait_for_idle(apps=[app], status="active", idle_period=30)

    # Check whether writes are increasing.
    logger.info("checking whether writes are increasing")
    primary_name = await get_primary(ops_test, app)
    await are_writes_increasing(ops_test, primary_name)

    # Verify that no writes to the database were missed after stopping the writes
    # (check that all the units have all the writes).
    logger.info("checking whether no writes were lost")
    await check_writes(ops_test)

    # check that all units were really upgraded.

    # Check the number of switchovers.
    final_number_of_switchovers = await count_switchovers(ops_test, primary_name)
    assert (final_number_of_switchovers - initial_number_of_switchovers) <= 2


# # async def test_failed_upgrade(ops_test: OpsTest, continuous_writes) -> None:
# #     # # Try a failing upgrade.
# #     # with open("./src/dependency.json", 'r+') as file:
# #     #     content = json.load(file)
# #     #     file.seek(0)
# #     #     original_version = int(content["charm"]["version"])
# #     #     print(original_version)
# #     #     content["charm"]["upgrade_supported"] = f">{original_version + 1}"
# #     #     content["charm"]["version"] = str(original_version + 2)
# #     #     json.dump(content, file, indent=2)
# #     #
# #     # # Run the pre-upgrade-check action.
# #     # logger.info("running pre-upgrade check")
# #     # leader_unit_name = None
# #     # for unit in ops_test.model.applications[app].units:
# #     #     if await unit.is_leader_from_status():
# #     #         leader_unit_name = unit.name
# #     #         break
# #     # action = await ops_test.model.units.get(leader_unit_name).run_action("pre-upgrade-check")
# #     # await action.wait()
# #     # assert action.results["Code"] == "0"
# #     #
# #     # # Run juju refresh.
# #     # logger.info("refreshing the charm")
# #     # application = ops_test.model.applications[app]
# #     # charm = await ops_test.build_charm(".")
# #     # await application.refresh(path=charm)
# #     # async with ops_test.fast_forward():
# #     #     await ops_test.model.wait_for_idle(apps=[app], status="active")
# #     pass
