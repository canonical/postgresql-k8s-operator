# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
import shutil
import zipfile
from pathlib import Path
from time import sleep

import pytest
import tomli
import tomli_w
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

TIMEOUT = 600


@pytest.mark.abort_on_fail
async def test_deploy_latest(ops_test: OpsTest) -> None:
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
        #     channel="16/edge",
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
            apps=[DATABASE_APP_NAME, APPLICATION_NAME],
            status="active",
            timeout=1000,
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
async def test_upgrade_from_edge(ops_test: OpsTest, charm, continuous_writes) -> None:
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

    if "Refresh incompatible" in refresh_order[0].workload_status_message:
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

    logger.info("checking the number of switchovers")
    final_number_of_switchovers = await count_switchovers(ops_test, primary_name)
    assert (final_number_of_switchovers - initial_number_of_switchovers) <= 2, (
        "Number of switchovers is greater than 2"
    )


@pytest.mark.abort_on_fail
async def test_fail_and_rollback(ops_test, charm, continuous_writes) -> None:
    # Start an application that continuously writes data to the database.
    logger.info("starting continuous writes to the database")
    await start_continuous_writes(ops_test, DATABASE_APP_NAME)

    # Check whether writes are increasing.
    logger.info("checking whether writes are increasing")
    await are_writes_increasing(ops_test)

    logger.info("Get leader unit")
    leader_unit = await get_leader_unit(ops_test, DATABASE_APP_NAME)
    assert leader_unit is not None, "No leader unit found"

    logger.info("Run pre-refresh-check action")

    action = await leader_unit.run_action("pre-refresh-check")
    await action.wait()

    filename = Path(charm).name
    fault_charm = Path("/tmp", f"{filename}.fault.charm")
    shutil.copy(charm, fault_charm)

    logger.info("Inject dependency fault")
    await inject_dependency_fault(fault_charm)

    application = ops_test.model.applications[DATABASE_APP_NAME]

    logger.info("Refresh the charm")
    await application.refresh(path=fault_charm)

    logger.info("Wait for upgrade to fail")

    # Highest to lowest unit number
    refresh_order = sorted(
        application.units, key=lambda unit: int(unit.name.split("/")[1]), reverse=True
    )

    await ops_test.model.block_until(
        lambda: application.status == "blocked"
        and "Refresh incompatible" in refresh_order[0].workload_status_message,
        timeout=TIMEOUT,
    )

    logger.info("Ensure continuous_writes while in failure state on remaining units")
    await are_writes_increasing(ops_test)

    logger.info("Re-refresh the charm")
    await application.refresh(path=charm)

    logger.info("Wait for upgrade to start")

    await ops_test.model.block_until(lambda: application.status == "blocked", timeout=TIMEOUT)

    logger.info("Wait for application to recover")
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

    logger.info("Ensure continuous_writes after rollback procedure")
    await are_writes_increasing(ops_test)

    # Verify that no writes to the database were missed after stopping the writes
    # (check that all the units have all the writes).
    logger.info("Checking whether no writes were lost")
    await check_writes(ops_test)

    # Remove fault charm file.
    fault_charm.unlink()


async def inject_dependency_fault(charm_file: str | Path) -> None:
    """Inject a dependency fault into the PostgreSQL charm."""
    with Path("refresh_versions.toml").open("rb") as file:
        versions = tomli.load(file)

    versions["charm"] = "16/0.0.0"
    versions["workload"] = "16.10"

    # Overwrite refresh_versions.toml with incompatible version.
    with zipfile.ZipFile(charm_file, mode="a") as charm_zip:
        charm_zip.writestr("refresh_versions.toml", tomli_w.dumps(versions))
