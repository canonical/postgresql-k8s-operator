#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import time

import pytest
from pytest_operator.plugin import OpsTest

from tests.helpers import METADATA
from tests.integration.helpers import (
    check_patroni,
    get_password,
    restart_patroni,
    set_password,
)

APP_NAME = METADATA["name"]


@pytest.mark.abort_on_fail
@pytest.mark.password_rotation
@pytest.mark.skip_if_deployed
async def test_deploy_active(ops_test: OpsTest):
    """Build the charm and deploy it."""
    charm = await ops_test.build_charm(".")
    async with ops_test.fast_forward():
        await ops_test.model.deploy(
            charm,
            resources={
                "postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"]
            },
            application_name=APP_NAME,
            num_units=3,
            trust=True,
        )
        await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)


@pytest.mark.password_rotation
async def test_password_rotation(ops_test: OpsTest):
    """Test password rotation action."""
    # Get the initial passwords set for the system users.
    superuser_password = await get_password(ops_test)
    replication_password = await get_password(ops_test, "replication")

    # Get the leader unit name (because passwords can only be set through it).
    leader = None
    for unit in ops_test.model.applications[APP_NAME].units:
        if await unit.is_leader_from_status():
            leader = unit.name
            break

    # Change both passwords.
    result = await set_password(ops_test, unit_name=leader)
    assert "operator-password" in result.keys()
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    result = await set_password(ops_test, unit_name=leader, username="replication")
    assert "replication-password" in result.keys()
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    new_superuser_password = await get_password(ops_test)
    new_replication_password = await get_password(ops_test, "replication")

    assert superuser_password != new_superuser_password
    assert replication_password != new_replication_password

    # Restart Patroni on any non-leader unit and check that
    # Patroni and PostgreSQL continue to work.
    restart_time = time.time()
    for unit in ops_test.model.applications[APP_NAME].units:
        if not await unit.is_leader_from_status():
            await restart_patroni(ops_test, unit.name)
            assert await check_patroni(ops_test, unit.name, restart_time)
