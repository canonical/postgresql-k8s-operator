# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging

import pytest
from lightkube import Client
from lightkube.resources.apps_v1 import StatefulSet
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_attempt, wait_fixed

from .. import markers
from ..helpers import (
    APPLICATION_NAME,
    DATABASE_APP_NAME,
    METADATA,
    count_switchovers,
    get_leader_unit,
    get_primary,
    get_unit_by_index,
)
from .helpers import (
    are_writes_increasing,
    check_writes,
    start_continuous_writes,
)

logger = logging.getLogger(__name__)

TIMEOUT = 5 * 60


@pytest.mark.group(1)
@markers.amd64_only  # TODO: remove after arm64 stable release
@pytest.mark.abort_on_fail
async def test_deploy_stable(ops_test: OpsTest) -> None:
    """Simple test to ensure that the PostgreSQL and application charms get deployed."""
    await asyncio.gather(
        ops_test.model.deploy(
            DATABASE_APP_NAME,
            num_units=3,
            channel="14/stable",
            trust=True,
        ),
        ops_test.model.deploy(
            APPLICATION_NAME,
            num_units=1,
            channel="latest/edge",
        ),
    )
    logger.info("Wait for applications to become active")
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME, APPLICATION_NAME], status="active"
        )
    assert len(ops_test.model.applications[DATABASE_APP_NAME].units) == 3


@pytest.mark.group(1)
@markers.amd64_only  # TODO: remove after arm64 stable release
@pytest.mark.abort_on_fail
async def test_pre_upgrade_check(ops_test: OpsTest) -> None:
    """Test that the pre-upgrade-check action runs successfully."""
    application = ops_test.model.applications[DATABASE_APP_NAME]
    if "pre-upgrade-check" not in await application.get_actions():
        logger.info("skipping the test because the charm from 14/stable doesn't support upgrade")
        return

    logger.info("Get leader unit")
    leader_unit = await get_leader_unit(ops_test, DATABASE_APP_NAME)
    assert leader_unit is not None, "No leader unit found"

    for attempt in Retrying(stop=stop_after_attempt(2), wait=wait_fixed(30), reraise=True):
        with attempt:
            logger.info("Run pre-upgrade-check action")
            action = await leader_unit.run_action("pre-upgrade-check")
            await action.wait()

            # Ensure the primary has changed to the first unit.
            primary_name = await get_primary(ops_test, DATABASE_APP_NAME)
            assert primary_name == f"{DATABASE_APP_NAME}/0", "Primary unit not set to unit 0"

    logger.info("Assert partition is set to 2")
    client = Client()
    stateful_set = client.get(
        res=StatefulSet, namespace=ops_test.model.info.name, name=DATABASE_APP_NAME
    )

    assert stateful_set.spec.updateStrategy.rollingUpdate.partition == 2, "Partition not set to 2"


@pytest.mark.group(1)
@markers.amd64_only  # TODO: remove after arm64 stable release
@pytest.mark.abort_on_fail
async def test_upgrade_from_stable(ops_test: OpsTest, continuous_writes):
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
    actions = await application.get_actions()

    logger.info("Build charm locally")
    charm = await ops_test.build_charm(".")

    logger.info("Refresh the charm")
    await application.refresh(path=charm, resources=resources)

    logger.info("Wait for upgrade to complete on first upgrading unit")
    # Highest ordinal unit always the first to upgrade.
    unit = get_unit_by_index(DATABASE_APP_NAME, application.units, 2)

    async with ops_test.fast_forward("60s"):
        await ops_test.model.block_until(
            lambda: unit.workload_status_message == "upgrade completed", timeout=TIMEOUT
        )
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME], idle_period=30, timeout=TIMEOUT
        )

    if "resume-upgrade" in actions:
        logger.info("Resume upgrade")
        leader_unit = await get_leader_unit(ops_test, DATABASE_APP_NAME)
        action = await leader_unit.run_action("resume-upgrade")
        await action.wait()

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
    if "pre-upgrade-check" in actions:
        logger.info("checking the number of switchovers")
        final_number_of_switchovers = await count_switchovers(ops_test, primary_name)
        assert (
            final_number_of_switchovers - initial_number_of_switchovers
        ) <= 2, "Number of switchovers is greater than 2"
