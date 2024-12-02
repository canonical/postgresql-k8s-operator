#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import pytest
from pytest_operator.plugin import OpsTest

from ..helpers import app_name, build_and_deploy
from .helpers import get_cluster_roles


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

    if wait_for_apps:
        async with ops_test.fast_forward():
            await ops_test.model.wait_for_idle(status="active", timeout=1000, raise_on_error=False)


@pytest.mark.group(1)
async def test_default_all(ops_test: OpsTest) -> None:
    app = await app_name(ops_test)

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(apps=[app], status="active")

    roles = get_cluster_roles(ops_test, ops_test.model.applications[app].units[0].name)

    assert len(roles["primaries"]) == 1
    assert len(roles["sync_standbys"]) == 2
    assert len(roles["replicas"]) == 0


@pytest.mark.group(1)
async def test_minority(ops_test: OpsTest) -> None:
    """Kill primary unit, check reelection."""
    app = await app_name(ops_test)

    await ops_test.model.applications[app].set_config({"synchronous_node_count": "minority"})

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(apps=[app], status="active")

    roles = get_cluster_roles(ops_test, ops_test.model.applications[app].units[0].name)

    assert len(roles["primaries"]) == 1
    assert len(roles["sync_standbys"]) == 1
    assert len(roles["replicas"]) == 1


@pytest.mark.group(1)
async def test_majority(ops_test: OpsTest) -> None:
    """Kill primary unit, check reelection."""
    app = await app_name(ops_test)

    await ops_test.model.applications[app].set_config({"synchronous_node_count": "majority"})

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(apps=[app], status="active")

    roles = get_cluster_roles(ops_test, ops_test.model.applications[app].units[0].name)

    assert len(roles["primaries"]) == 1
    assert len(roles["sync_standbys"]) == 2
    assert len(roles["replicas"]) == 0


@pytest.mark.group(1)
async def test_constant(ops_test: OpsTest) -> None:
    """Kill primary unit, check reelection."""
    app = await app_name(ops_test)

    await ops_test.model.applications[app].set_config({"synchronous_node_count": "1"})

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(apps=[app], status="active")

    roles = get_cluster_roles(ops_test, ops_test.model.applications[app].units[0].name)

    assert len(roles["primaries"]) == 1
    assert len(roles["sync_standbys"]) == 1
    assert len(roles["replicas"]) == 1
