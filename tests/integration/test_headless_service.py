#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import time

import pytest
from lightkube.core.client import Client
from lightkube.core.exceptions import ApiError
from lightkube.resources.core_v1 import Service
from pytest_operator.plugin import OpsTest

from .helpers import DATABASE_APP_NAME, build_and_deploy

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_deploy(ops_test: OpsTest, charm):
    """Deploy the charm and wait for active/idle."""
    await build_and_deploy(ops_test, charm, num_units=3)


async def test_headless_service_recreated(ops_test: OpsTest):
    """Delete the headless endpoints service and verify the charm recreates it."""
    model_name = ops_test.model.info.name
    svc_name = f"{DATABASE_APP_NAME}-endpoints"

    # Verify the headless service exists.
    client = Client(namespace=model_name)
    svc = client.get(Service, name=svc_name)
    assert svc.spec.clusterIP == "None"

    # Delete the headless service.
    logger.info("Deleting headless service %s", svc_name)
    client.delete(Service, name=svc_name)

    # Verify it's gone.
    with pytest.raises(ApiError, match="not found"):
        client.get(Service, name=svc_name)

    # Speed up update-status.
    await ops_test.juju("model-config", "update-status-hook-interval=1m")

    # Poll for the service to be recreated (avoids websocket timeout issues
    # caused by the headless service deletion disrupting K8s networking).
    deadline = time.time() + 600
    recreated = False
    while time.time() < deadline:
        try:
            svc = client.get(Service, name=svc_name)
            if svc.spec.clusterIP == "None":
                recreated = True
                break
        except ApiError:
            pass
        time.sleep(10)

    assert recreated, f"Headless service {svc_name} was not recreated within timeout"
    logger.info("Headless service %s was recreated", svc_name)

    # Wait for units to settle back to active/idle.
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME],
        status="active",
        timeout=300,
    )

    # Restore default interval.
    await ops_test.juju("model-config", "update-status-hook-interval=5m")
