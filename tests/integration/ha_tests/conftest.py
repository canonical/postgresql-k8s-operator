#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import pytest as pytest
from pytest_operator.plugin import OpsTest


@pytest.fixture()
async def continuous_writes(ops_test: OpsTest) -> None:
    """Deploy the charm that makes continuous writes to PostgreSQL."""
    charm = await ops_test.build_charm("tests/integration/ha_tests/application-charm")
    async with ops_test.fast_forward():
        await ops_test.model.deploy(charm)
        await ops_test.model.wait_for_idle(status="active", timeout=1000)
    yield
    # await clear_db_writes(ops_test)
    pass
