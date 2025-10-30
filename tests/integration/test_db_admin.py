#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio

import pytest as pytest
from pytest_operator.plugin import OpsTest

from . import markers
from .helpers import (
    DATABASE_APP_NAME,
    build_and_deploy,
    check_database_creation,
    check_database_users_existence,
    get_unit_address,
)

DISCOURSE_APP_NAME = "discourse-charmers-discourse-k8s"
REDIS_APP_NAME = "redis-k8s"
APPLICATION_UNITS = 1
DATABASE_UNITS = 3


@markers.amd64_only  # discourse-charmers-discourse-k8s charm contains amd64-only binaries (pyyaml)
@pytest.mark.abort_on_fail
async def test_discourse_from_discourse_charmers(ops_test: OpsTest, charm):
    # Build and deploy charm from local source folder (and also redis from Charmhub).
    # Both are needed by Discourse.
    async with ops_test.fast_forward():
        await asyncio.gather(
            build_and_deploy(ops_test, charm, DATABASE_UNITS),
            ops_test.model.deploy(
                REDIS_APP_NAME,
                application_name=REDIS_APP_NAME,
                channel="latest/edge",
                base="ubuntu@22.04",
            ),
        )
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME, REDIS_APP_NAME], status="active", timeout=1500
        )

    # Get the Redis instance IP address.
    redis_host = await get_unit_address(ops_test, f"{REDIS_APP_NAME}/0")

    # Deploy Discourse and wait for it to be blocked waiting for database relation.
    await ops_test.model.deploy(
        DISCOURSE_APP_NAME,
        application_name=DISCOURSE_APP_NAME,
        config={
            "redis_host": redis_host,
            "developer_emails": "user@foo.internal",
            "external_hostname": "foo.internal",
            "smtp_address": "127.0.0.1",
            "smtp_domain": "foo.internal",
        },
    )
    # Discourse becomes blocked waiting for PostgreSQL relation.
    await ops_test.model.wait_for_idle(apps=[DISCOURSE_APP_NAME], status="blocked", timeout=1000)

    # Relate PostgreSQL and Discourse, waiting for Discourse to be ready.
    relation = await ops_test.model.add_relation(
        f"{DATABASE_APP_NAME}:db-admin",
        DISCOURSE_APP_NAME,
    )
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME, DISCOURSE_APP_NAME, REDIS_APP_NAME],
        status="active",
        timeout=2000,  # Discourse takes a longer time to become active (a lot of setup).
    )

    # Check for the correct databases and users creation.
    await check_database_creation(ops_test, "discourse-charmers-discourse-k8s")
    discourse_users = [f"relation_id_{relation.id}"]
    await check_database_users_existence(ops_test, discourse_users, [], admin=True)

    # Remove Discourse relation and validate that related users were deleted
    await ops_test.model.applications[DATABASE_APP_NAME].remove_relation(
        f"{DATABASE_APP_NAME}:db-admin", f"{DISCOURSE_APP_NAME}"
    )
    await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1000)
    await check_database_users_existence(ops_test, [], discourse_users)

    # Remove the deployment of Discourse.
    await ops_test.model.remove_application(DISCOURSE_APP_NAME, block_until_done=True)
