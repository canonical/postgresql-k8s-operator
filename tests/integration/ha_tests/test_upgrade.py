# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
import shutil
from pathlib import Path

import pytest
from lightkube import Client
from lightkube.resources.apps_v1 import StatefulSet
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_attempt, wait_fixed

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
    inject_dependency_fault,
    start_continuous_writes,
)

logger = logging.getLogger(__name__)

TIMEOUT = 600


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_deploy_latest(ops_test: OpsTest) -> None:
    """Simple test to ensure that the PostgreSQL and application charms get deployed."""
    await asyncio.gather(
        ops_test.model.deploy(
            DATABASE_APP_NAME,
            num_units=3,
            channel="14/edge",
            trust=True,
            config={"profile": "testing"},
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
            apps=[DATABASE_APP_NAME, APPLICATION_NAME],
            status="active",
            raise_on_error=False,
            timeout=1000,
        )
    assert len(ops_test.model.applications[DATABASE_APP_NAME].units) == 3


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_pre_upgrade_check(ops_test: OpsTest) -> None:
    """Test that the pre-upgrade-check action runs successfully."""
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
@pytest.mark.abort_on_fail
async def test_upgrade_from_edge(ops_test: OpsTest, continuous_writes) -> None:
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

    logger.info("Build charm locally")
    charm = await ops_test.build_charm(".")

    logger.info("Refresh the charm")
    await application.refresh(path=charm, resources=resources)

    logger.info("Wait for upgrade to complete on first upgrading unit")
    # highest ordinal unit always the first to upgrade
    unit = get_unit_by_index(DATABASE_APP_NAME, application.units, 2)

    async with ops_test.fast_forward("60s"):
        await ops_test.model.block_until(
            lambda: unit.workload_status_message == "upgrade completed", timeout=TIMEOUT
        )
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME], idle_period=30, timeout=TIMEOUT
        )

    # Check whether writes are increasing.
    logger.info("checking whether writes are increasing")
    await are_writes_increasing(ops_test)

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

    logger.info("checking the number of switchovers")
    final_number_of_switchovers = await count_switchovers(ops_test, primary_name)
    assert (
        final_number_of_switchovers - initial_number_of_switchovers
    ) <= 2, "Number of switchovers is greater than 2"


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_fail_and_rollback(ops_test, continuous_writes) -> None:
    # Start an application that continuously writes data to the database.
    logger.info("starting continuous writes to the database")
    await start_continuous_writes(ops_test, DATABASE_APP_NAME)

    # Check whether writes are increasing.
    logger.info("checking whether writes are increasing")
    await are_writes_increasing(ops_test)

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
            assert primary_name == f"{DATABASE_APP_NAME}/0"

    local_charm = await ops_test.build_charm(".")
    filename = local_charm.split("/")[-1] if isinstance(local_charm, str) else local_charm.name
    fault_charm = Path("/tmp/", filename)
    shutil.copy(local_charm, fault_charm)

    logger.info("Inject dependency fault")
    await inject_dependency_fault(ops_test, DATABASE_APP_NAME, fault_charm)

    application = ops_test.model.applications[DATABASE_APP_NAME]

    logger.info("Refresh the charm")
    await application.refresh(path=fault_charm)

    logger.info("Get first upgrading unit")
    # Highest ordinal unit always the first to upgrade.
    unit = get_unit_by_index(DATABASE_APP_NAME, application.units, 2)

    logger.info("Wait for upgrade to fail on first upgrading unit")
    async with ops_test.fast_forward("60s"):
        await ops_test.model.block_until(
            lambda: unit.workload_status == "blocked",
            timeout=TIMEOUT,
        )

    logger.info("Ensure continuous_writes while in failure state on remaining units")
    await are_writes_increasing(ops_test)

    logger.info("Re-run pre-upgrade-check action")
    action = await leader_unit.run_action("pre-upgrade-check")
    await action.wait()

    logger.info("Re-refresh the charm")
    await application.refresh(path=local_charm)

    async with ops_test.fast_forward("60s"):
        await ops_test.model.block_until(
            lambda: unit.workload_status_message == "upgrade completed", timeout=TIMEOUT
        )
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME], idle_period=30, timeout=TIMEOUT
        )

    # Check whether writes are increasing.
    logger.info("checking whether writes are increasing")
    await are_writes_increasing(ops_test)

    logger.info("Resume upgrade")
    action = await leader_unit.run_action("resume-upgrade")
    await action.wait()

    logger.info("Wait for application to recover")
    async with ops_test.fast_forward("60s"):
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME], status="active", timeout=TIMEOUT
        )

    logger.info("Ensure continuous_writes after rollback procedure")
    await are_writes_increasing(ops_test)

    # Verify that no writes to the database were missed after stopping the writes
    # (check that all the units have all the writes).
    logger.info("Checking whether no writes were lost")
    await check_writes(ops_test)

    # Remove fault charm file.
    fault_charm.unlink()
