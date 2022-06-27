#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio
import logging

import pytest
from pytest_operator.plugin import OpsTest

from tests.helpers import METADATA
from tests.integration.helpers import TLS_RESOURCES, attach_resource

logger = logging.getLogger(__name__)

APPLICATION_NAME = "mattermost-k8s"
DATABASE_NAME = METADATA["name"]


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    """Build the charm-under-test and deploy it.

    # TODO: move to conftest.py.

    Assert on the unit status before any relations/configurations take place.
    """
    # Build and deploy charm from local source folder (and also mattermost-k8s from Charmhub).
    charm = await ops_test.build_charm(".")
    resources = {
        "postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"],
        "cert-file": METADATA["resources"]["cert-file"]["filename"],
        "key-file": METADATA["resources"]["key-file"]["filename"],
    }
    await asyncio.gather(
        ops_test.model.deploy(
            charm, resources=resources, application_name=DATABASE_NAME, trust=True  # , num_units=3
        ),
        ops_test.model.deploy(APPLICATION_NAME, application_name=APPLICATION_NAME),
    )
    await ops_test.model.wait_for_idle(apps=[DATABASE_NAME], status="active", timeout=1000)


async def test_old_db_relation(ops_test: OpsTest):
    await ops_test.model.set_config({"update-status-hook-interval": "5s"})

    print(TLS_RESOURCES.items())
    for rsc_name, src_path in TLS_RESOURCES.items():
        print(f"rsc_name: {rsc_name} - src_path: {src_path}")
        await attach_resource(ops_test, DATABASE_NAME, rsc_name, src_path)


    # FIXME: A wait here is not guaranteed to work. It can succeed before resources
    # have been added. Additionally, attaching resources can result on transient error
    # states for the application while is stabilizing again.
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_NAME],
        status="active",
        idle_period=30,
        raise_on_blocked=False,
        raise_on_error=False,
        timeout=1000,
    )

    await ops_test.model.add_relation(
        f"{DATABASE_NAME}:db",
        APPLICATION_NAME,
    )
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_NAME, APPLICATION_NAME], status="active", timeout=1000
    )
