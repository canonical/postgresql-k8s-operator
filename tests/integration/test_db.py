#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio
import logging

import pytest
from pytest_operator.plugin import OpsTest

from tests.helpers import METADATA

# from tests.integration.helpers import TLS_RESOURCES, attach_resource, get_unit_address

logger = logging.getLogger(__name__)

FINOS_WALTZ_APP_NAME = "finos-waltz-k8s"
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
        ops_test.model.deploy(
            FINOS_WALTZ_APP_NAME, application_name=FINOS_WALTZ_APP_NAME, channel="edge"
        ),
    )
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_NAME],
        status="active",
        timeout=1000,
    )


async def test_finos_waltz_relation(ops_test: OpsTest):
    await ops_test.model.set_config({"update-status-hook-interval": "5s"})

    await ops_test.model.add_relation(
        f"{DATABASE_NAME}:db",
        FINOS_WALTZ_APP_NAME,
    )

    await ops_test.model.wait_for_idle(
        apps=[DATABASE_NAME, FINOS_WALTZ_APP_NAME], status="active", timeout=1000
    )
