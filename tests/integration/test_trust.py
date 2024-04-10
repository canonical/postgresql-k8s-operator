#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest
from pytest_operator.plugin import OpsTest

from .helpers import (
    METADATA,
    get_leader_unit,
)

logger = logging.getLogger(__name__)

APP_NAME = "untrusted-postgres-k8s"
UNTRUST_ERROR_MESSAGE = "Unauthorized access to k8s resources. Is the app trusted? See logs"


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_deploy_without_trust(ops_test: OpsTest):
    """Build and deploy the charm with trust set to false.

    Assert on the unit status being blocked due to lack of trust.
    """
    charm = await ops_test.build_charm(".")
    await ops_test.run("sudo", "microk8s", "enable", "rbac")

    async with ops_test.fast_forward():
        await ops_test.model.deploy(
            charm,
            resources={
                "postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"]
            },
            application_name=APP_NAME,
            num_units=3,
            trust=False,
        )

        await ops_test.model.block_until(
            lambda: any(
                unit.workload_status == "blocked"
                for unit in ops_test.model.applications[APP_NAME].units
            ),
            timeout=1000,
        )

        leader_unit = await get_leader_unit(ops_test, APP_NAME)
        assert leader_unit.workload_status == "blocked"
        assert leader_unit.workload_status_message == UNTRUST_ERROR_MESSAGE


@pytest.mark.group(1)
async def test_trust_blocked_deployment(ops_test: OpsTest):
    """Trust existing blocked deployment.

    Assert on the application status recovering to active.
    """
    await ops_test.juju("trust", APP_NAME, "--scope=cluster")

    app = ops_test.model.applications[APP_NAME]
    await ops_test.model.block_until(lambda: app.status in ("active", "error"), timeout=1000)
    assert app.status == "active"
