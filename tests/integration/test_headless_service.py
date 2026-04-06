#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest
from lightkube.core.client import Client
from lightkube.core.exceptions import ApiError
from lightkube.models.core_v1 import ServiceSpec
from lightkube.models.meta_v1 import ObjectMeta
from lightkube.resources.core_v1 import Service
from pytest_operator.plugin import OpsTest

from .helpers import DATABASE_APP_NAME, build_and_deploy

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_deploy(ops_test: OpsTest, charm):
    """Deploy the charm and wait for active/idle."""
    await build_and_deploy(ops_test, charm, num_units=3)


async def test_headless_service_error_and_recovery(ops_test: OpsTest):
    """Delete headless service, verify error state, recreate, and recover."""
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

    # Speed up update-status so the charm detects the missing service.
    async with ops_test.fast_forward():
        # Wait for at least one unit to go into error state.
        await ops_test.model.block_until(
            lambda: any(
                unit.workload_status == "error"
                for unit in ops_test.model.applications[DATABASE_APP_NAME].units
            ),
            timeout=600,
        )
        logger.info("Unit(s) entered error state as expected")

    # Recreate the headless service (simulating the operator's manual fix).
    logger.info("Recreating headless service %s", svc_name)
    client.apply(
        Service(
            metadata=ObjectMeta(
                name=svc_name,
                namespace=model_name,
                labels={
                    "app.kubernetes.io/name": DATABASE_APP_NAME,
                    "app.kubernetes.io/managed-by": "juju",
                },
            ),
            spec=ServiceSpec(
                clusterIP="None",
                publishNotReadyAddresses=True,
                selector={"app.kubernetes.io/name": DATABASE_APP_NAME},
            ),
        ),
        field_manager="integration-test",
        force=True,
    )

    # Verify it's back.
    svc = client.get(Service, name=svc_name)
    assert svc.spec.clusterIP == "None"
    logger.info("Headless service recreated")

    # Resolve all units in error state.
    for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
        if unit.workload_status == "error":
            logger.info("Resolving %s", unit.name)
            await unit.resolved(retry=False)

    # Wait for units to settle back to active/idle.
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME],
        status="active",
        raise_on_error=False,
        timeout=600,
    )
