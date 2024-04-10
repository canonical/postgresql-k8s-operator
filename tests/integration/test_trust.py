#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
import time

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
async def test_enable_rbac(ops_test: OpsTest):
    """Build and deploy the charm with trust set to false.

    Assert on the unit status being blocked due to lack of trust.
    """
    proc = await asyncio.create_subprocess_exec(
        "sudo",
        "microk8s",
        "enable",
        "rbac",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, stderr = await proc.communicate()
    message, err = stdout.decode(), stderr.decode()
    logger.info(f"{message}")
    logger.info(f"{err}")

    # procc = await asyncio.create_subprocess_exec(
    #     "microk8s",
    #     "kubectl",
    #     "-n",
    #     "kube-system",
    #     "rollout",
    #     "status", deployment/coredns
    #     stdout=asyncio.subprocess.PIPE,
    #     stderr=asyncio.subprocess.PIPE,
    # )

    # stdout, stderr = await procc.communicate()
    # logger.info(f"{stdout.decode()}")
    # logger.info(f"{stderr.decode()}")

    time.sleep(3)

    proc2 = await asyncio.create_subprocess_exec(
        "microk8s",
        "status",
        "--wait-ready",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout2, stderr2 = await proc2.communicate()
    message, err = stdout2.decode(), stderr2.decode()
    logger.info(f"{message}")
    logger.info(f"{err}")

    time.sleep(3)

    assert "rbac" in message.split("disabled")[0]

@pytest.mark.group(1)
async def test_model_connectivity(ops_test: OpsTest):
    """Tries to regain connectivity to model """
    max_retries = 20
    retries = 0

    while retries < max_retries:
        try:
            # Attempt to run the await statements sequentially
            await ops_test.model.connect_current()
            status = await ops_test.model.get_status()
            logger.info(f"status encontrado = {status}")
            assert True
            return
        except Exception as e:
            logger.info(f"Exception occurred: {e}")
            retries += 1
            logger.info(f"Retrying ({retries}/{max_retries})...")
            time.sleep(3)

    print("Max retries reached. Unable to complete the operation.")
    assert False


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_deploy_without_trust(ops_test: OpsTest):
    """Build and deploy the charm with trust set to false.

    Assert on the unit status being blocked due to lack of trust.
    """
    charm = await ops_test.build_charm(".")

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
