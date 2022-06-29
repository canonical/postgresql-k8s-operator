#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio
import logging

import pytest
from pytest_operator.plugin import OpsTest

from tests.helpers import METADATA

# from tests.integration.helpers import TLS_RESOURCES, attach_resource, get_unit_address
from tests.integration.helpers import get_unit_address

logger = logging.getLogger(__name__)

FIRST_DISCOURSE_APP_NAME = "discourse-k8s"
SECOND_DISCOURSE_APP_NAME = "discourse-charmers-discourse-k8s"
REDIS_APP_NAME = "redis-k8s"
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
        ops_test.model.deploy(FIRST_DISCOURSE_APP_NAME, application_name=FIRST_DISCOURSE_APP_NAME),
        ops_test.model.deploy(REDIS_APP_NAME, application_name=REDIS_APP_NAME),
    )
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_NAME, REDIS_APP_NAME], status="active", timeout=1000
    )


async def test_old_db_admin_relation(ops_test: OpsTest):
    await ops_test.model.set_config({"update-status-hook-interval": "5s"})

    await asyncio.gather(
        ops_test.model.add_relation(
            f"{DATABASE_NAME}:db-admin",
            FIRST_DISCOURSE_APP_NAME,
        ),
        ops_test.model.add_relation(
            REDIS_APP_NAME,
            FIRST_DISCOURSE_APP_NAME,
        ),
    )

    await ops_test.model.wait_for_idle(
        apps=[DATABASE_NAME, FIRST_DISCOURSE_APP_NAME, REDIS_APP_NAME],
        status="active",
        timeout=1000,
    )


async def test_discourse_relation(ops_test: OpsTest):
    await ops_test.model.set_config({"update-status-hook-interval": "5s"})

    redis_host = await get_unit_address(ops_test, REDIS_APP_NAME, f"{REDIS_APP_NAME}/0")

    await ops_test.model.deploy(
        SECOND_DISCOURSE_APP_NAME,
        application_name=SECOND_DISCOURSE_APP_NAME,
        config={
            "redis_host": redis_host,
            "developer_emails": "user@foo.internal",
            "external_hostname": "foo.internal",
            "smtp_address": "127.0.0.1",
            "smtp_domain": "foo.internal",
        },
    )
    await ops_test.model.wait_for_idle(
        apps=[SECOND_DISCOURSE_APP_NAME], status="blocked", timeout=1000
    )

    await asyncio.gather(
        ops_test.model.add_relation(
            f"{DATABASE_NAME}:db-admin",
            SECOND_DISCOURSE_APP_NAME,
        )
    )

    await ops_test.model.wait_for_idle(
        apps=[DATABASE_NAME, SECOND_DISCOURSE_APP_NAME, REDIS_APP_NAME],
        status="active",
        timeout=1000,
    )
