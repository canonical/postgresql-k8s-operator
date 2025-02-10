#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
import time

import pytest
from pytest_operator.plugin import OpsTest

from .helpers import (
    KUBECTL,
    METADATA,
    get_leader_unit,
)

logger = logging.getLogger(__name__)

APP_NAME = "untrusted-postgresql-k8s"
MAX_RETRIES = 20
UNTRUST_ERROR_MESSAGE = f"Insufficient permissions, try: `juju trust {APP_NAME} --scope=cluster`"


@pytest.mark.group(1)
async def test_enable_rbac(ops_test: OpsTest):
    """Enables RBAC from inside test runner's environment.

    Assert on permission enforcement being active.
    """
    enable_rbac_call = await asyncio.create_subprocess_exec(
        "sudo",
        "microk8s",
        "enable",
        "rbac",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await enable_rbac_call.communicate()

    is_default_auth = None
    retries = 0
    while is_default_auth != "no" and retries < MAX_RETRIES:
        rbac_check = await asyncio.create_subprocess_exec(
            *KUBECTL.split(),
            "auth",
            "can-i",
            "get",
            "cm",
            "-A",
            "--as=system:serviceaccount:default:no-permissions",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await rbac_check.communicate()
        if stdout:
            is_default_auth = stdout.decode().split()[0]
            logger.info(f"Response from rbac check ('no' means enabled): {is_default_auth}")
        retries += 1

    assert is_default_auth == "no"


@pytest.mark.group(1)
async def test_model_connectivity(ops_test: OpsTest):
    """Tries to regain connectivity to model after microK8s restart."""
    retries = 0
    while retries < MAX_RETRIES:
        try:
            await ops_test.model.connect_current()
            status = await ops_test.model.get_status()
            logger.info(f"Connection established: {status}")
            return
        except Exception as e:
            logger.info(f"Connection attempt failed: {e}")
            retries += 1
            logger.info(f"Retrying ({retries}/{MAX_RETRIES})...")
            time.sleep(3)

    logger.error(f"Max retries number of {MAX_RETRIES} reached. Unable to connect.")
    assert False


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_deploy_without_trust(ops_test: OpsTest, database_charm):
    """Build and deploy the charm with trust set to false.

    Assert on the unit status being blocked due to lack of trust.
    """
    await ops_test.model.deploy(
        database_charm,
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

    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)
