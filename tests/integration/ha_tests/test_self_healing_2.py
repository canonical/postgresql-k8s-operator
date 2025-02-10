# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import pytest
from pytest_operator.plugin import OpsTest

from ..helpers import (
    APPLICATION_NAME,
    CHARM_BASE,
    METADATA,
    app_name,
    build_and_deploy,
    get_unit_address,
    scale_application,
)
from .helpers import (
    are_writes_increasing,
    check_writes,
    fetch_cluster_members,
    is_postgresql_ready,
    start_continuous_writes,
)

logger = logging.getLogger(__name__)

APP_NAME = METADATA["name"]
PATRONI_PROCESS = "/usr/bin/patroni"
POSTGRESQL_PROCESS = "postgres"
DB_PROCESSES = [POSTGRESQL_PROCESS, PATRONI_PROCESS]
MEDIAN_ELECTION_TIME = 10


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest, charm) -> None:
    """Build and deploy three unit of PostgreSQL."""
    wait_for_apps = False
    # It is possible for users to provide their own cluster for HA testing. Hence, check if there
    # is a pre-existing cluster.
    if not await app_name(ops_test):
        wait_for_apps = True
        await build_and_deploy(ops_test, charm, 3, wait_for_idle=False)
    # Deploy the continuous writes application charm if it wasn't already deployed.
    if not await app_name(ops_test, APPLICATION_NAME):
        wait_for_apps = True
        async with ops_test.fast_forward():
            await ops_test.model.deploy(
                APPLICATION_NAME,
                application_name=APPLICATION_NAME,
                base=CHARM_BASE,
                channel="edge",
            )

    if wait_for_apps:
        async with ops_test.fast_forward():
            await ops_test.model.wait_for_idle(status="active", timeout=1000, raise_on_error=False)


@pytest.mark.abort_on_fail
async def test_scaling_to_zero(ops_test: OpsTest, continuous_writes) -> None:
    """Scale the database to zero units and scale up again."""
    # Deploy applications
    await test_build_and_deploy(ops_test)

    # Locate primary unit.
    app = await app_name(ops_test)

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # Scale the database to zero units.
    logger.info("scaling database to zero units")
    await scale_application(ops_test, app, 0)

    # Scale the database to three units.
    logger.info("scaling database to three units")
    await scale_application(ops_test, app, 3)

    # Verify all units are up and running.
    logger.info("waiting for the database service to start in all units")
    for unit in ops_test.model.applications[app].units:
        assert await is_postgresql_ready(ops_test, unit.name), (
            f"unit {unit.name} not restarted after cluster restart."
        )

    logger.info("checking whether writes are increasing")
    await are_writes_increasing(ops_test)

    # Verify that all units are part of the same cluster.
    logger.info("checking whether all units are part of the same cluster")
    member_ips = await fetch_cluster_members(ops_test)
    ip_addresses = [
        await get_unit_address(ops_test, unit.name)
        for unit in ops_test.model.applications[app].units
    ]
    assert set(member_ips) == set(ip_addresses), "not all units are part of the same cluster."

    # Verify that no writes to the database were missed after stopping the writes.
    logger.info("checking whether no writes to the database were missed after stopping the writes")
    await check_writes(ops_test)
