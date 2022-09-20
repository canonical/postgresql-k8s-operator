#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import pytest as pytest
from pytest_operator.plugin import OpsTest

from tests.integration.ha_tests.helpers import app_name

APPLICATION_NAME = "application"


@pytest.fixture()
async def continuous_writes(ops_test: OpsTest) -> None:
    """Deploy the charm that makes continuous writes to PostgreSQL."""
    # Deploy the continuous writes application charm if it wasn't already deployed.
    async with ops_test.fast_forward():
        if await app_name(ops_test, APPLICATION_NAME) is None:
            charm = await ops_test.build_charm("tests/integration/ha_tests/application-charm")
            await ops_test.model.deploy(charm, application_name=APPLICATION_NAME)
            await ops_test.model.wait_for_idle(status="active", timeout=1000)
    yield
    # Clear the written data at the end.
    action = await ops_test.model.units.get(f"{APPLICATION_NAME}/0").run_action(
        "clear-continuous-writes"
    )
    await action.wait()
