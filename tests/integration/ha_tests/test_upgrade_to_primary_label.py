# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
import operator

import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_attempt, wait_fixed

from .. import markers
from ..architecture import architecture
from ..helpers import (
    APPLICATION_NAME,
    CHARM_BASE,
    CHARM_SERIES,
    DATABASE_APP_NAME,
    METADATA,
    get_leader_unit,
    get_primary,
    get_unit_by_index,
)
from ..juju_ import juju_major_version
from .helpers import (
    are_writes_increasing,
    check_writes,
    get_instances_roles,
    start_continuous_writes,
)

logger = logging.getLogger(__name__)

TIMEOUT = 600


@pytest.mark.group(1)
@pytest.mark.unstable
@markers.amd64_only  # TODO: remove after arm64 stable release
@pytest.mark.abort_on_fail
async def test_deploy_stable(ops_test: OpsTest) -> None:
    """Simple test to ensure that the PostgreSQL and application charms get deployed."""
    database_additional_params = {}
    if juju_major_version >= 3:
        database_additional_params["base"] = CHARM_BASE
    else:
        database_additional_params["series"] = CHARM_SERIES

    await asyncio.gather(
        ops_test.model.deploy(
            DATABASE_APP_NAME,
            num_units=3,
            channel="16/stable",
            revision=(280 if architecture == "arm64" else 281),
            trust=True,
            **database_additional_params,
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
            apps=[DATABASE_APP_NAME, APPLICATION_NAME], status="active", raise_on_error=False
        )
    assert len(ops_test.model.applications[DATABASE_APP_NAME].units) == 3
    instances_roles = await get_instances_roles(ops_test)
    assert operator.countOf(instances_roles.values(), "master") == 1
    assert operator.countOf(instances_roles.values(), "primary") == 0
    assert operator.countOf(instances_roles.values(), "replica") == 2


@pytest.mark.group(1)
@pytest.mark.unstable
@markers.amd64_only  # TODO: remove after arm64 stable release
async def test_upgrade(ops_test, continuous_writes) -> None:
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
    application = ops_test.model.applications[DATABASE_APP_NAME]

    resources = {"postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"]}
    application = ops_test.model.applications[DATABASE_APP_NAME]

    logger.info("Refresh the charm")
    await application.refresh(path=local_charm, resources=resources)

    logger.info("Get first upgrading unit")
    # Highest ordinal unit always the first to upgrade.
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

    instances_roles = await get_instances_roles(ops_test)
    assert operator.countOf(instances_roles.values(), "master") == 1
    assert operator.countOf(instances_roles.values(), "primary") == 0
    assert operator.countOf(instances_roles.values(), "replica") == 2

    logger.info("Resume upgrade")
    action = await leader_unit.run_action("resume-upgrade")
    await action.wait()

    logger.info("Wait for application to upgrade")
    async with ops_test.fast_forward("60s"):
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME], status="active", timeout=TIMEOUT
        )

    instances_roles = await get_instances_roles(ops_test)
    assert operator.countOf(instances_roles.values(), "master") == 0
    assert operator.countOf(instances_roles.values(), "primary") == 1
    assert operator.countOf(instances_roles.values(), "replica") == 2

    logger.info("Ensure continuous_writes after upgrade")
    await are_writes_increasing(ops_test)

    # Verify that no writes to the database were missed after stopping the writes
    # (check that all the units have all the writes).
    logger.info("Checking whether no writes were lost")
    await check_writes(ops_test)
