#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio
import logging
import time

import psycopg2
import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from ..helpers import (
    CHARM_SERIES,
    METADATA,
    check_database_creation,
    check_database_users_existence,
)
from ..new_relations.test_new_relations import (
    APPLICATION_APP_NAME,
    DATABASE_APP_METADATA,
    build_connection_string,
)

logger = logging.getLogger(__name__)

APP_NAME = METADATA["name"]
FINOZ_WALTZ_APP_NAME = "finos-waltz-k8s"
DB_RELATION = "db"
DATABASE_RELATION = "database"
FIRST_DATABASE_RELATION = "first-database"
APP_NAMES = [APP_NAME, APPLICATION_APP_NAME]


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_deploy_charms(ops_test: OpsTest, database_charm):
    """Deploy both charms (application and database) to use in the tests."""
    # Deploy both charms (multiple units for each application to test that later they correctly
    # set data in the relation application databag using only the leader unit).
    async with ops_test.fast_forward():
        await asyncio.gather(
            ops_test.model.deploy(
                APPLICATION_APP_NAME,
                application_name=APPLICATION_APP_NAME,
                num_units=1,
                series=CHARM_SERIES,
                channel="edge",
            ),
            ops_test.model.deploy(
                database_charm,
                resources={
                    "postgresql-image": DATABASE_APP_METADATA["resources"]["postgresql-image"][
                        "upstream-source"
                    ]
                },
                application_name=APP_NAME,
                num_units=1,
                series=CHARM_SERIES,
                config={"profile": "testing"},
            ),
        )

        await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=3000)

        await ops_test.model.deploy(
            FINOZ_WALTZ_APP_NAME,
            channel="edge",
            application_name=FINOZ_WALTZ_APP_NAME,
            num_units=1,
        )
        await ops_test.model.wait_for_idle(
            apps=[FINOZ_WALTZ_APP_NAME],
            status="blocked",
            raise_on_blocked=False,
            timeout=1000,
        )


@pytest.mark.group(1)
async def test_legacy_endpoint_with_multiple_related_endpoints(ops_test: OpsTest):
    relation = await ops_test.model.relate(FINOZ_WALTZ_APP_NAME, f"{APP_NAME}:{DB_RELATION}")
    await ops_test.model.wait_for_idle(
        status="active",
        timeout=3000,
        raise_on_error=False,
    )

    app = ops_test.model.applications[APP_NAME]
    async with ops_test.fast_forward():
        await ops_test.model.relate(APP_NAME, f"{APPLICATION_APP_NAME}:{FIRST_DATABASE_RELATION}")
        await ops_test.model.block_until(
            lambda: "blocked" in {unit.workload_status for unit in app.units},
            timeout=1500,
        )

    # Sleep for a while to allow the relation to be established.
    time.sleep(10)
    await ops_test.model.applications[APP_NAME].destroy_relation(
        f"{APP_NAME}:{DATABASE_RELATION}", f"{APPLICATION_APP_NAME}:{FIRST_DATABASE_RELATION}"
    )
    await ops_test.model.wait_for_idle(
        status="active",
        timeout=3000,
        raise_on_error=False,
    )

    logger.info(" check database creation 'waltz'")
    await check_database_creation(ops_test, "waltz")

    finos_waltz_users = [f"relation_id_{relation.id}"]
    logger.info(f" check database users existence '{finos_waltz_users}'")
    await check_database_users_existence(ops_test, finos_waltz_users, [])

    logger.info(
        f" remove relation: {FINOZ_WALTZ_APP_NAME}:{DB_RELATION} - {APP_NAME}:{DB_RELATION}"
    )
    await ops_test.model.applications[APP_NAME].remove_relation(
        f"{FINOZ_WALTZ_APP_NAME}:{DB_RELATION}", f"{APP_NAME}:{DB_RELATION}"
    )
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)
    logger.info(f" check database users not existence '{finos_waltz_users}'")
    await check_database_users_existence(ops_test, [], finos_waltz_users)


@pytest.mark.group(1)
async def test_modern_endpoint_with_multiple_related_endpoints(ops_test: OpsTest):
    async with ops_test.fast_forward():
        await ops_test.model.relate(FINOZ_WALTZ_APP_NAME, f"{APP_NAME}:{DB_RELATION}")
        await ops_test.model.wait_for_idle(
            status="active",
            timeout=3000,
            raise_on_error=False,
        )

    app = ops_test.model.applications[APP_NAME]
    async with ops_test.fast_forward():
        await ops_test.model.relate(APP_NAME, f"{APPLICATION_APP_NAME}:{FIRST_DATABASE_RELATION}")
        await ops_test.model.block_until(
            lambda: "blocked" in {unit.workload_status for unit in app.units},
            timeout=1500,
        )

    # Sleep for a while to allow the relation to be established.
    time.sleep(10)
    async with ops_test.fast_forward():
        await ops_test.model.applications[APP_NAME].remove_relation(
            f"{FINOZ_WALTZ_APP_NAME}:{DB_RELATION}", f"{APP_NAME}:{DB_RELATION}"
        )
        await ops_test.model.wait_for_idle(
            apps=[FINOZ_WALTZ_APP_NAME],
            status="blocked",
            raise_on_blocked=False,
            timeout=1000,
        )

    modern_interface_connect = await build_connection_string(
        ops_test, APPLICATION_APP_NAME, FIRST_DATABASE_RELATION
    )
    logger.info(f" check connect to = {modern_interface_connect}")
    for attempt in Retrying(stop=stop_after_delay(60 * 3), wait=wait_fixed(10)):
        with attempt:
            with psycopg2.connect(modern_interface_connect) as connection:
                assert connection.status == psycopg2.extensions.STATUS_READY

    logger.info(f" remove relation {APPLICATION_APP_NAME}")
    async with ops_test.fast_forward():
        await ops_test.model.applications[APP_NAME].remove_relation(
            f"{APP_NAME}:{DATABASE_RELATION}", f"{APPLICATION_APP_NAME}:{FIRST_DATABASE_RELATION}"
        )
        await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)
        for attempt in Retrying(stop=stop_after_delay(60 * 5), wait=wait_fixed(10)):
            with attempt:
                with pytest.raises(psycopg2.OperationalError):
                    psycopg2.connect(modern_interface_connect)
