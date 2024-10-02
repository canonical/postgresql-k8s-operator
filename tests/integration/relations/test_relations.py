#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio
import logging

import pytest
from pytest_operator.plugin import OpsTest

from ..helpers import CHARM_BASE
from ..new_relations.test_new_relations import (
    APPLICATION_APP_NAME,
    DATABASE_APP_METADATA,
)
from ..relations.helpers import (
    APP_NAME,
    DATABASE_RELATION,
    DB_RELATION,
    FIRST_DATABASE_RELATION,
)

logger = logging.getLogger(__name__)


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
                base=CHARM_BASE,
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
                base=CHARM_BASE,
                config={
                    "profile": "testing",
                    "plugin_unaccent_enable": "True",
                    "plugin_pg_trgm_enable": "True",
                },
            ),
        )

        await ops_test.model.wait_for_idle(
            apps=[APP_NAME, APPLICATION_APP_NAME], status="active", timeout=3000
        )


@pytest.mark.group(1)
async def test_legacy_and_modern_endpoints_simultaneously(ops_test: OpsTest):
    await ops_test.model.relate(APPLICATION_APP_NAME, f"{APP_NAME}:{DB_RELATION}")
    await ops_test.model.wait_for_idle(
        status="active",
        timeout=1500,
        raise_on_error=False,
    )

    logger.info(" add relation with modern endpoints")
    app = ops_test.model.applications[APP_NAME]
    async with ops_test.fast_forward():
        await ops_test.model.relate(APP_NAME, f"{APPLICATION_APP_NAME}:{FIRST_DATABASE_RELATION}")
        await ops_test.model.block_until(
            lambda: "blocked" in {unit.workload_status for unit in app.units},
            timeout=1500,
        )

    logger.info(" remove relation with legacy endpoints")
    await ops_test.model.applications[APP_NAME].destroy_relation(
        f"{APP_NAME}:{DB_RELATION}", f"{APPLICATION_APP_NAME}:{DB_RELATION}"
    )
    await ops_test.model.wait_for_idle(status="active", timeout=1500)

    logger.info(" add relation with legacy endpoints")
    async with ops_test.fast_forward():
        await ops_test.model.relate(APPLICATION_APP_NAME, f"{APP_NAME}:{DB_RELATION}")
        await ops_test.model.block_until(
            lambda: "blocked" in {unit.workload_status for unit in app.units},
            timeout=1500,
        )

    logger.info(" remove relation with modern endpoints")
    await ops_test.model.applications[APP_NAME].destroy_relation(
        f"{APP_NAME}:{DATABASE_RELATION}", f"{APPLICATION_APP_NAME}:{FIRST_DATABASE_RELATION}"
    )
    await ops_test.model.wait_for_idle(status="active", timeout=1500)

    logger.info(" remove relation with legacy endpoints")
    await ops_test.model.applications[APP_NAME].destroy_relation(
        f"{APP_NAME}:{DB_RELATION}", f"{APPLICATION_APP_NAME}:{DB_RELATION}"
    )
    await ops_test.model.wait_for_idle(status="active", timeout=1500)

    logger.info(" add relation with modern endpoints")
    await ops_test.model.relate(APP_NAME, f"{APPLICATION_APP_NAME}:{FIRST_DATABASE_RELATION}")
    await ops_test.model.wait_for_idle(status="active", timeout=1500)
