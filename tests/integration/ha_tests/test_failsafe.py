# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio
import logging
import time

import pytest
import requests
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from .. import markers
from ..helpers import (
    APPLICATION_NAME,
    DATABASE_APP_NAME,
    app_name,
    build_and_deploy,
    count_switchovers,
    execute_query_on_unit,
    get_password,
    get_unit_address,
    run_command_on_unit,
)
from .helpers import (
    are_writes_increasing,
    check_writes,
    cut_dcs_access,
    get_patroni_setting,
    get_primary,
    isolate_instance_from_cluster,
    remove_instance_isolation,
    restore_dcs_access,
    start_continuous_writes,
)

logger = logging.getLogger(__name__)


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
                channel="latest/edge",
                series="jammy",
            )

    if wait_for_apps:
        await ops_test.model.relate(DATABASE_APP_NAME, f"{APPLICATION_NAME}:database")
        await ops_test.model.wait_for_idle(
            apps=[
                APPLICATION_NAME,
                DATABASE_APP_NAME,
            ],
            status="active",
            timeout=1000,
            idle_period=30,
        )


@pytest.mark.abort_on_fail
@markers.amd64_only
async def test_primary_still_alive(ops_test: OpsTest, continuous_writes, chaos_mesh) -> None:
    """Check that the primary keeps its role while the DCS is not accessible."""
    app = await app_name(ops_test)
    primary_name = await get_primary(ops_test, app)
    primary_ip = await get_unit_address(ops_test, primary_name)

    assert await get_patroni_setting(ops_test, "failsafe_mode")
    initial_switchovers = await count_switchovers(ops_test, primary_name)
    ttl = await get_patroni_setting(ops_test, "ttl") or 30
    # The password comes from a Juju secret, which can't be read while the K8s API is cut.
    password = await get_password(ops_test)

    logger.info("starting continuous writes to the database")
    await start_continuous_writes(ops_test, app)

    # Halts the execution of `update-status` hook, as the charm also depends on the K8s API.
    async with ops_test.fast_forward("24h"):
        try:
            logger.info("cutting the access to the K8s API")
            cut_dcs_access(ops_test, app)

            # grep exits with a non-zero code (making the helper raise) until Patroni logs that
            # it entered the failsafe mode. The dots in the pattern stand for spaces, as the
            # helper splits the command on whitespaces.
            logger.info(f"waiting for {primary_name} to enter the failsafe mode")
            for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3), reraise=True):
                with attempt:
                    await run_command_on_unit(
                        ops_test,
                        primary_name,
                        "grep -rh failsafe.mode.is.enabled /var/log/postgresql",
                    )

            # The primary must keep its role during the whole outage, not only at the end of it.
            logger.info(f"checking that {primary_name} keeps being the primary for {ttl * 2}s")
            deadline = time.monotonic() + ttl * 2
            while time.monotonic() < deadline:
                response = requests.get(f"http://{primary_ip}:8008/primary", timeout=5)
                assert response.status_code == 200, f"{primary_name} is no longer the primary"
                await asyncio.sleep(3)

            logger.info("checking whether writes are increasing")
            query = "SELECT COUNT(number) FROM continuous_writes;"
            database = f"{APPLICATION_NAME.replace('-', '_')}_database"
            writes = await execute_query_on_unit(primary_ip, password, query, database)
            await asyncio.sleep(10)
            more_writes = await execute_query_on_unit(primary_ip, password, query, database)
            assert more_writes > writes, "writes are not increasing"

        finally:
            logger.info("restoring the access to the K8s API")
            restore_dcs_access(ops_test)

    logger.info("waiting for the cluster to settle")
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[app], status="active", idle_period=30, timeout=10 * 60, raise_on_error=False
        )

    logger.info("checking that no switchover or failover happened")
    assert await get_primary(ops_test, app) == primary_name
    assert await count_switchovers(ops_test, primary_name) == initial_switchovers
    logger.info("checking whether no writes were lost")
    await check_writes(ops_test)


@pytest.mark.abort_on_fail
@markers.amd64_only
async def test_primary_demoted_without_replica(
    ops_test: OpsTest, continuous_writes, chaos_mesh
) -> None:
    """Check that the primary is demoted if it can't reach a replica while the DCS is down."""
    app = await app_name(ops_test)
    primary_name = await get_primary(ops_test, app)
    primary_ip = await get_unit_address(ops_test, primary_name)
    replica_name = next(
        unit.name for unit in ops_test.model.applications[app].units if unit.name != primary_name
    )

    logger.info("starting continuous writes to the database")
    await start_continuous_writes(ops_test, app)

    # Halts the execution of `update-status` hook, as the charm also depends on the K8s API.
    async with ops_test.fast_forward("24h"):
        try:
            # The DCS is cut first, so the primary can't remove the replica from the list of
            # members that it must reach to keep its role.
            logger.info("cutting the access to the K8s API")
            cut_dcs_access(ops_test, app)

            logger.info(f"cutting network for {replica_name}")
            isolate_instance_from_cluster(ops_test, replica_name)

            logger.info(f"waiting for {primary_name} to be demoted")
            for attempt in Retrying(
                stop=stop_after_delay(60 * 2), wait=wait_fixed(3), reraise=True
            ):
                with attempt:
                    response = requests.get(f"http://{primary_ip}:8008/primary", timeout=5)
                    assert response.status_code != 200, f"{primary_name} is still the primary"

            # The dots in the pattern stand for spaces, as the helper splits the command on
            # whitespaces.
            logger.info(f"checking that {primary_name} was demoted because of the DCS failure")
            for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3), reraise=True):
                with attempt:
                    await run_command_on_unit(
                        ops_test,
                        primary_name,
                        "grep -rh DCS.is.not.accessible.and.I.was.a.leader /var/log/postgresql",
                    )

        finally:
            logger.info(f"restoring network for {replica_name}")
            remove_instance_isolation(ops_test)
            logger.info("restoring the access to the K8s API")
            restore_dcs_access(ops_test)

    logger.info("waiting for the cluster to settle")
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[app], status="active", idle_period=30, timeout=10 * 60, raise_on_error=False
        )

    logger.info("checking whether writes are increasing")
    await are_writes_increasing(ops_test)
    logger.info("checking whether no writes were lost")
    await check_writes(ops_test)
