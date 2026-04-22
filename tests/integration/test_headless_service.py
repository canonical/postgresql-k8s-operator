#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import jubilant
import pytest
from lightkube.core.client import Client
from lightkube.core.exceptions import ApiError
from lightkube.models.core_v1 import ServiceSpec
from lightkube.models.meta_v1 import ObjectMeta
from lightkube.resources.core_v1 import Service

from .helpers import DATABASE_APP_NAME, METADATA

logger = logging.getLogger(__name__)

TIMEOUT = 20 * 60


def database_active_idle(status: jubilant.Status) -> bool:
    return jubilant.all_active(status, DATABASE_APP_NAME) and jubilant.all_agents_idle(
        status, DATABASE_APP_NAME
    )


def any_database_unit_error(status: jubilant.Status) -> bool:
    app = status.apps.get(DATABASE_APP_NAME)
    if app is None:
        return False

    return any(unit.workload_status.current == "error" for unit in app.units.values())


@pytest.mark.abort_on_fail
def test_deploy(juju: jubilant.Juju, charm) -> None:
    """Deploy the charm and wait for active/idle."""
    if DATABASE_APP_NAME not in juju.status().apps:
        resources = {
            "postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"]
        }
        juju.deploy(
            charm,
            config={"profile": "testing"},
            num_units=3,
            resources=resources,
            trust=True,
        )

    juju.wait(database_active_idle, timeout=TIMEOUT)


def test_headless_service_error_and_recovery(juju: jubilant.Juju) -> None:
    """Delete headless service, verify error state, recreate, and recover."""
    model_name = juju.model
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
    juju.model_config(values={"update-status-hook-interval": "1m"})
    try:
        # Wait for at least one unit to go into error state.
        juju.wait(any_database_unit_error, timeout=TIMEOUT)
        logger.info("Unit(s) entered error state as expected")
    finally:
        juju.model_config(reset=("update-status-hook-interval",))

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
    for unit_name, unit_status in juju.status().apps[DATABASE_APP_NAME].units.items():
        if unit_status.workload_status.current == "error":
            logger.info("Resolving %s", unit_name)
            juju.cli("resolve", "--no-retry", unit_name)

    # Wait for units to settle back to active/idle.
    juju.wait(database_active_idle, timeout=TIMEOUT)
