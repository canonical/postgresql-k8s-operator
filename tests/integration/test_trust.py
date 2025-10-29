#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio
import logging

import pytest
from pytest_operator.plugin import OpsTest

from .helpers import (
    CHARM_BASE,
    METADATA,
)

logger = logging.getLogger(__name__)

APP_NAME = "untrusted-postgresql-k8s"
UNTRUST_ERROR_MESSAGE = (
    f"Run `juju trust {APP_NAME} --scope=cluster`. Needed for in-place refreshes"
)


@pytest.mark.abort_on_fail
async def test_deploy_without_trust(ops_test: OpsTest, charm):
    """Build and deploy the charm with trust set to false.

    Assert on the unit status being blocked due to lack of trust.
    """
    await ops_test.model.deploy(
        charm,
        resources={
            "postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"]
        },
        application_name=APP_NAME,
        num_units=3,
        trust=False,
        base=CHARM_BASE,
    )

    logger.info("Waiting for charm to become blocked due to missing --trust")
    await asyncio.gather(
        ops_test.model.block_until(
            lambda: ops_test.model.applications[APP_NAME].status == "blocked", timeout=300
        ),
        ops_test.model.block_until(
            lambda: ops_test.model.applications[APP_NAME].status_message == UNTRUST_ERROR_MESSAGE,
            timeout=1000,
        ),
    )


async def test_trust_blocked_deployment(ops_test: OpsTest):
    """Trust existing blocked deployment.

    Assert on the application status recovering to active.
    """
    await ops_test.juju("trust", APP_NAME, "--scope=cluster")

    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1500)
