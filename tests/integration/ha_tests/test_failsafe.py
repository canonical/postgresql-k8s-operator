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
    check_writes,
    cut_dcs_access,
    get_patroni_setting,
    get_primary,
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

    await start_continuous_writes(ops_test, app)

    # Halts the execution of `update-status` hook, as the charm also depends on the K8s API.
    async with ops_test.fast_forward("24h"):
        try:
            cut_dcs_access(ops_test, app)

            # grep exits with a non-zero code (making the helper raise) until Patroni logs that
            # it entered the failsafe mode. The dots in the pattern stand for spaces, as the
            # helper splits the command on whitespaces.
            for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3), reraise=True):
                with attempt:
                    await run_command_on_unit(
                        ops_test,
                        primary_name,
                        "grep -rh failsafe.mode.is.enabled /var/log/postgresql",
                    )

            # The primary must keep its role during the whole outage, not only at the end of it.
            deadline = time.monotonic() + ttl * 2
            while time.monotonic() < deadline:
                response = requests.get(f"http://{primary_ip}:8008/primary", timeout=5)
                assert response.status_code == 200, f"{primary_name} is no longer the primary"
                await asyncio.sleep(3)

            query = "SELECT COUNT(number) FROM continuous_writes;"
            database = f"{APPLICATION_NAME.replace('-', '_')}_database"
            writes = await execute_query_on_unit(primary_ip, password, query, database)
            await asyncio.sleep(10)
            more_writes = await execute_query_on_unit(primary_ip, password, query, database)
            assert more_writes > writes, "writes are not increasing"

        finally:
            restore_dcs_access(ops_test)

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[app], status="active", idle_period=30, timeout=10 * 60, raise_on_error=False
        )

    assert await get_primary(ops_test, app) == primary_name
    assert await count_switchovers(ops_test, primary_name) == initial_switchovers
    await check_writes(ops_test)
