# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
from time import sleep

import pytest
from pytest_operator.plugin import OpsTest

from ..helpers import (
    APPLICATION_NAME,
    CHARM_BASE_NOBLE,
    DATABASE_APP_NAME,
    METADATA,
    count_switchovers,
    get_leader_unit,
    get_primary,
)
from .helpers import (
    are_writes_increasing,
    check_writes,
    start_continuous_writes,
)

logger = logging.getLogger(__name__)

TIMEOUT = 10 * 60


@pytest.mark.abort_on_fail
async def test_deploy_stable(ops_test: OpsTest) -> None:
    """Simple test to ensure that the PostgreSQL and application charms get deployed."""
    await asyncio.gather(
        # TODO: remove call to ops_test.juju and uncomment call to ops_test.model.deploy.
        ops_test.juju(
            "deploy",
            DATABASE_APP_NAME,
            "-n",
            3,
            "--channel",
            "16/edge/neppel",
            "--trust",
            "--config",
            "profile=testing",
            "--base",
            CHARM_BASE_NOBLE,
        ),
        # ops_test.model.deploy(
        #     DATABASE_APP_NAME,
        #     num_units=3,
        #     channel="16/stable",
        #     trust=True,
        #     config={"profile": "testing"},
        #     base=CHARM_BASE_NOBLE,
        # ),
        ops_test.model.deploy(
            APPLICATION_NAME,
            num_units=1,
            channel="latest/edge",
            config={"sleep_interval": 500},
        ),
    )
    await ops_test.model.relate(DATABASE_APP_NAME, f"{APPLICATION_NAME}:database")
    logger.info("Wait for applications to become active")
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME, APPLICATION_NAME], status="active", timeout=(20 * 60)
        )
    assert len(ops_test.model.applications[DATABASE_APP_NAME].units) == 3


@pytest.mark.abort_on_fail
async def test_pre_refresh_check(ops_test: OpsTest) -> None:
    """Test that the pre-refresh-check action runs successfully."""
    logger.info("Get leader unit")
    leader_unit = await get_leader_unit(ops_test, DATABASE_APP_NAME)
    assert leader_unit is not None, "No leader unit found"

    logger.info("Run pre-refresh-check action")
    action = await leader_unit.run_action("pre-refresh-check")
    await action.wait()


@pytest.mark.abort_on_fail
async def test_upgrade_from_stable(ops_test: OpsTest, charm):
    """Test updating from stable channel."""
    # Start an application that continuously writes data to the database.
    logger.info("starting continuous writes to the database")
    await start_continuous_writes(ops_test, DATABASE_APP_NAME)

    # Check whether writes are increasing.
    logger.info("checking whether writes are increasing")
    await are_writes_increasing(ops_test)

    primary_name = await get_primary(ops_test, DATABASE_APP_NAME)
    initial_number_of_switchovers = await count_switchovers(ops_test, primary_name)

    resources = {"postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"]}
    application = ops_test.model.applications[DATABASE_APP_NAME]

    logger.info("Refresh the charm")
    await application.refresh(path=charm, resources=resources)

    logger.info("Wait for upgrade to start")
    await ops_test.model.block_until(lambda: application.status == "blocked", timeout=60 * 3)

    logger.info("Wait for refresh to block as paused or incompatible")
    async with ops_test.fast_forward("60s"):
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME], idle_period=30, timeout=TIMEOUT
        )

    # Highest to lowest unit number
    refresh_order = sorted(
        application.units, key=lambda unit: int(unit.name.split("/")[1]), reverse=True
    )

    if "Refresh incompatible" in application.status_message:
        logger.info("Application refresh is blocked due to incompatibility")

        action = await refresh_order[0].run_action(
            "force-refresh-start", **{"check-compatibility": False}
        )
        await action.wait()

        logger.info("Wait for first incompatible unit to upgrade")
        async with ops_test.fast_forward("60s"):
            await ops_test.model.wait_for_idle(
                apps=[DATABASE_APP_NAME], idle_period=30, timeout=TIMEOUT
            )
    else:
        async with ops_test.fast_forward("60s"):
            await ops_test.model.block_until(
                lambda: all(unit.workload_status == "active" for unit in application.units),
                timeout=60 * 3,
            )

    sleep(60)

    leader_unit = await get_leader_unit(ops_test, DATABASE_APP_NAME)
    logger.info(f"Run resume-refresh action on {leader_unit.name}")
    action = await leader_unit.run_action("resume-refresh")
    await action.wait()
    logger.info(f"Results from the action: {action.results}")

    logger.info("Wait for upgrade to complete")
    async with ops_test.fast_forward("60s"):
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME], status="active", idle_period=30, timeout=TIMEOUT
        )

    # Check whether writes are increasing.
    logger.info("checking whether writes are increasing")
    await are_writes_increasing(ops_test)

    # Verify that no writes to the database were missed after stopping the writes
    # (check that all the units have all the writes).
    logger.info("checking whether no writes were lost")
    await check_writes(ops_test)

    # Check the number of switchovers.
    logger.info("checking the number of switchovers")
    final_number_of_switchovers = await count_switchovers(ops_test, primary_name)
    assert (final_number_of_switchovers - initial_number_of_switchovers) <= 2, (
        "Number of switchovers is greater than 2"
    )
