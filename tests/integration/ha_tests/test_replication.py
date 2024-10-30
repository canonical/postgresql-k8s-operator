#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import pytest
from lightkube.core.client import Client
from lightkube.resources.core_v1 import Pod
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from ..helpers import (
    APPLICATION_NAME,
    CHARM_SERIES,
    app_name,
    build_and_deploy,
    db_connect,
    get_password,
    get_primary,
    get_unit_address,
    scale_application,
)
from .helpers import (
    are_writes_increasing,
    check_writes,
    is_cluster_updated,
    start_continuous_writes,
)


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest) -> None:
    """Build and deploy three unit of PostgreSQL."""
    wait_for_apps = False
    # It is possible for users to provide their own cluster for HA testing. Hence, check if there
    # is a pre-existing cluster.
    if not await app_name(ops_test):
        wait_for_apps = True
        await build_and_deploy(ops_test, 3, wait_for_idle=False)
    # Deploy the continuous writes application charm if it wasn't already deployed.
    if not await app_name(ops_test, APPLICATION_NAME):
        wait_for_apps = True
        async with ops_test.fast_forward():
            await ops_test.model.deploy(
                APPLICATION_NAME,
                application_name=APPLICATION_NAME,
                series=CHARM_SERIES,
                channel="edge",
            )

    if wait_for_apps:
        async with ops_test.fast_forward():
            await ops_test.model.wait_for_idle(status="active", timeout=1000, raise_on_error=False)


@pytest.mark.group(1)
async def test_reelection(ops_test: OpsTest, continuous_writes, primary_start_timeout) -> None:
    """Kill primary unit, check reelection."""
    app = await app_name(ops_test)
    if len(ops_test.model.applications[app].units) < 2:
        await scale_application(ops_test, app, 2)

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # Kill the current primary.
    primary_name = await get_primary(ops_test, app)
    client = Client(namespace=ops_test.model.info.name)
    client.delete(Pod, name=primary_name.replace("/", "-"))

    # Wait and get the primary again (which can be any unit, including the previous primary).
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(apps=[app], status="active")

    # Check whether writes are increasing.
    await are_writes_increasing(ops_test, primary_name)

    # Verify that a new primary gets elected (ie old primary is secondary).
    for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
        with attempt:
            new_primary_name = await get_primary(ops_test, app, down_unit=primary_name)
            assert new_primary_name != primary_name, "primary reelection hasn't happened"

    # Verify that all the units from the first cluster are up-to-date.
    await is_cluster_updated(ops_test, primary_name)


@pytest.mark.group(1)
async def test_consistency(ops_test: OpsTest, continuous_writes) -> None:
    """Write to primary, read data from secondaries (check consistency)."""
    # Locate primary unit.
    app = await app_name(ops_test)
    primary_name = await get_primary(ops_test, app)

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # Check whether writes are increasing.
    await are_writes_increasing(ops_test, primary_name)

    # Verify that no writes to the database were missed after stopping the writes
    # (check that all the units have all the writes).
    await check_writes(ops_test)


@pytest.mark.group(1)
async def test_no_data_replicated_between_clusters(ops_test: OpsTest, continuous_writes) -> None:
    """Check that writes in one cluster are not replicated to another cluster."""
    # Locate primary unit.
    app = await app_name(ops_test)
    primary_name = await get_primary(ops_test, app)

    # Deploy another cluster.
    new_cluster_app = f"second-{app}"
    await build_and_deploy(ops_test, 2, database_app_name=new_cluster_app)

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # Check whether writes are increasing.
    await are_writes_increasing(ops_test, primary_name)

    # Verify that no writes to the first cluster were missed after stopping the writes.
    await check_writes(ops_test)

    # Verify that the data from the first cluster wasn't replicated to the second cluster.
    password = await get_password(ops_test, database_app_name=new_cluster_app)
    for unit in ops_test.model.applications[new_cluster_app].units:
        address = await get_unit_address(ops_test, unit.name)
        try:
            with db_connect(
                host=address, password=password
            ) as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT EXISTS (SELECT FROM information_schema.tables"
                    " WHERE table_schema = 'public' AND table_name = 'continuous_writes');"
                )
                assert not cursor.fetchone()[0], (
                    "table 'continuous_writes' was replicated to the second cluster"
                )
        finally:
            connection.close()
