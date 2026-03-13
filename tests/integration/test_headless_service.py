#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import time

import jubilant
import pytest
from jubilant import Juju
from lightkube.core.client import Client
from lightkube.core.exceptions import ApiError
from lightkube.resources.core_v1 import Service

from .helpers import CHARM_BASE_NOBLE, METADATA

logger = logging.getLogger(__name__)

DB_APP_NAME = METADATA["name"]
MINUTE_SECS = 60


def test_deploy(juju: Juju, charm: str):
    """Deploy the charm and wait for active/idle."""
    resources = {
        "postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"],
    }
    juju.deploy(
        charm=charm,
        app=DB_APP_NAME,
        base=CHARM_BASE_NOBLE,
        resources=resources,
        config={"profile": "testing"},
        num_units=3,
        trust=True,
    )
    juju.wait(jubilant.all_active, timeout=20 * MINUTE_SECS)


def test_headless_service_recreated(juju: Juju):
    """Delete the headless endpoints service and verify the charm recreates it."""
    # juju.model may include "controller:" prefix; K8s namespace is just the model name.
    model_name = juju.model.split(":")[-1]
    svc_name = f"{DB_APP_NAME}-endpoints"

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
    juju.cli("model-config", "update-status-hook-interval=1m")

    # Poll for the service to be recreated (avoids issues from the headless
    # service deletion disrupting K8s networking for Juju connections).
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
    juju.wait(jubilant.all_active, timeout=5 * MINUTE_SECS)

    # Restore default interval.
    juju.cli("model-config", "update-status-hook-interval=5m")
