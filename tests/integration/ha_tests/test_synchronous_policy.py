#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
from asyncio import exceptions

import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_attempt, wait_fixed

from ..helpers import app_name, build_and_deploy
from .helpers import get_cluster_roles

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

    if wait_for_apps:
        async with ops_test.fast_forward():
            await ops_test.model.wait_for_idle(status="active", timeout=1000, raise_on_error=False)


@pytest.mark.abort_on_fail
async def test_promote_to_primary(ops_test: OpsTest) -> None:
    """Switch the primary over to a sync standby and check the Primary label follows it."""
    app = await app_name(ops_test)
    database_app = ops_test.model.applications[app]
    any_unit_name = database_app.units[0].name
    original_roles = await get_cluster_roles(ops_test, any_unit_name)
    old_primary = original_roles["primaries"][0]
    new_primary = original_roles["sync_standbys"][0]

    def primary_labels() -> dict[str, str]:
        return {unit.name: unit.workload_status_message for unit in database_app.units}

    expected_labels = {
        unit.name: "Primary" if unit.name == new_primary else "" for unit in database_app.units
    }

    # The update-status hook recomputes the label on its own. Keep it from firing,
    # so the label has to be moved by the switchover handling itself.
    await ops_test.model.set_config({"update-status-hook-interval": "60m"})
    try:
        await ops_test.model.wait_for_idle(
            apps=[app], status="active", timeout=600, idle_period=30
        )
        assert primary_labels()[old_primary] == "Primary"

        logger.info(f"Promoting {new_primary} (current primary: {old_primary})")
        run_action = await ops_test.model.units[new_primary].run_action(
            "promote-to-primary", scope="unit"
        )
        await run_action.wait()
        assert run_action.status == "completed"

        try:
            await ops_test.model.block_until(
                lambda: primary_labels() == expected_labels, timeout=180
            )
        except exceptions.TimeoutError:
            pytest.fail(
                f"Primary label did not move from {old_primary} to {new_primary}: "
                f"{primary_labels()}"
            )
        await ops_test.model.wait_for_idle(
            apps=[app], status="active", timeout=600, idle_period=30
        )
        # The label must also stay put once the units settle.
        assert primary_labels() == expected_labels
    finally:
        await ops_test.model.set_config({"update-status-hook-interval": "5m"})

    new_roles = await get_cluster_roles(ops_test, any_unit_name)
    assert new_roles["primaries"] == [new_primary]
    assert old_primary in new_roles["sync_standbys"]
    assert len(new_roles["sync_standbys"]) == 2
    assert len(new_roles["replicas"]) == 0


async def test_default_all(ops_test: OpsTest) -> None:
    app = await app_name(ops_test)

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(apps=[app], status="active", timeout=300)

    for attempt in Retrying(stop=stop_after_attempt(3), wait=wait_fixed(5), reraise=True):
        with attempt:
            roles = await get_cluster_roles(
                ops_test, ops_test.model.applications[app].units[0].name
            )

            assert len(roles["primaries"]) == 1
            assert len(roles["sync_standbys"]) == 2
            assert len(roles["replicas"]) == 0


async def test_majority(ops_test: OpsTest) -> None:
    app = await app_name(ops_test)

    await ops_test.model.applications[app].set_config({"synchronous-node-count": "majority"})

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(apps=[app], status="active")

    for attempt in Retrying(stop=stop_after_attempt(3), wait=wait_fixed(5), reraise=True):
        with attempt:
            roles = await get_cluster_roles(
                ops_test, ops_test.model.applications[app].units[0].name
            )

            assert len(roles["primaries"]) == 1
            assert len(roles["sync_standbys"]) == 1
            assert len(roles["replicas"]) == 1


async def test_constant(ops_test: OpsTest) -> None:
    app = await app_name(ops_test)

    await ops_test.model.applications[app].set_config({"synchronous-node-count": "2"})

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(apps=[app], status="active", timeout=300)

    for attempt in Retrying(stop=stop_after_attempt(3), wait=wait_fixed(5), reraise=True):
        with attempt:
            roles = await get_cluster_roles(
                ops_test, ops_test.model.applications[app].units[0].name
            )

            assert len(roles["primaries"]) == 1
            assert len(roles["sync_standbys"]) == 2
            assert len(roles["replicas"]) == 0
