#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio
import logging

import pytest
from pytest_operator.plugin import OpsTest

from tests.helpers import METADATA
from tests.integration.helpers import TLS_RESOURCES, attach_resource, get_unit_address

logger = logging.getLogger(__name__)

MATTERMOST_APP_NAME = "mattermost-k8s"
FIRST_DISCOURSE_APP_NAME = "discourse-k8s"
SECOND_DISCOURSE_APP_NAME = "discourse-charmers-discourse-k8s"
REDIS_APP_NAME = "redis-k8s"
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
        # ops_test.model.deploy(MATTERMOST_APP_NAME, application_name=MATTERMOST_APP_NAME),
        # ops_test.model.deploy(FIRST_DISCOURSE_APP_NAME, application_name=FIRST_DISCOURSE_APP_NAME),
        ops_test.model.deploy(REDIS_APP_NAME, application_name=REDIS_APP_NAME),
        # ops_test.model.deploy(
        #     FINOS_WALTZ_APP_NAME, application_name=FINOS_WALTZ_APP_NAME, channel="edge"
        # ),
    )
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_NAME, REDIS_APP_NAME], status="active", timeout=1000
        # apps=[DATABASE_NAME],
        # status="active",
        # timeout=1000,
    )


# async def test_old_db_relation(ops_test: OpsTest):
#     await ops_test.model.set_config({"update-status-hook-interval": "5s"})
#
#     print(TLS_RESOURCES.items())
#     for rsc_name, src_path in TLS_RESOURCES.items():
#         print(f"rsc_name: {rsc_name} - src_path: {src_path}")
#         await attach_resource(ops_test, DATABASE_NAME, rsc_name, src_path)
#
#     # FIXME: A wait here is not guaranteed to work. It can succeed before resources
#     # have been added. Additionally, attaching resources can result on transient error
#     # states for the application while is stabilizing again.
#     await ops_test.model.wait_for_idle(
#         apps=[DATABASE_NAME],
#         status="active",
#         idle_period=30,
#         raise_on_blocked=False,
#         raise_on_error=False,
#         timeout=1000,
#     )
#
#     await ops_test.model.add_relation(
#         f"{DATABASE_NAME}:db",
#         MATTERMOST_APP_NAME,
#     )
#     await ops_test.model.wait_for_idle(
#         apps=[DATABASE_NAME, MATTERMOST_APP_NAME], status="active", timeout=1000
#     )
#
#
# async def test_old_db_admin_relation(ops_test: OpsTest):
#     await ops_test.model.set_config({"update-status-hook-interval": "5s"})
#
#     await asyncio.gather(
#         ops_test.model.add_relation(
#             f"{DATABASE_NAME}:db-admin",
#             DISCOURSE_APP_NAME,
#         ),
#         ops_test.model.add_relation(
#             REDIS_APP_NAME,
#             DISCOURSE_APP_NAME,
#         ),
#     )
#
#     await ops_test.model.wait_for_idle(
#         apps=[DATABASE_NAME, DISCOURSE_APP_NAME, REDIS_APP_NAME], status="active", timeout=1000
#     )


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
        }
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
        apps=[DATABASE_NAME, SECOND_DISCOURSE_APP_NAME, REDIS_APP_NAME], status="active", timeout=1000
    )
#
#
# async def test_finos_waltz_relation(ops_test: OpsTest):
#     await ops_test.model.set_config({"update-status-hook-interval": "5s"})
#
#     await ops_test.model.add_relation(
#         f"{DATABASE_NAME}:db",
#         FINOS_WALTZ_APP_NAME,
#     )
#
#     await ops_test.model.wait_for_idle(
#         apps=[DATABASE_NAME, FINOS_WALTZ_APP_NAME], status="active", timeout=1000
#     )
