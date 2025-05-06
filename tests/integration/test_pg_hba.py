#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest
from pytest_operator.plugin import OpsTest

from .helpers import (
    APPLICATION_NAME,
    DATABASE_APP_NAME,
    build_and_deploy,
    execute_query_on_unit,
    get_password,
    get_unit_address,
)

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest, charm) -> None:
    """Build and deploy two units of PostgreSQL."""
    await build_and_deploy(ops_test, charm, num_units=2, wait_for_idle=True)


@pytest.mark.abort_on_fail
async def test_pg_hba(ops_test: OpsTest):
    await ops_test.model.deploy(
        APPLICATION_NAME, config={"database-name": "test", "extra-user-roles": "SUPERUSER"}
    )

    async with ops_test.fast_forward():
        logger.info("Adding relation between charms")
        await ops_test.model.add_relation(f"{APPLICATION_NAME}:database", DATABASE_APP_NAME)
        await ops_test.model.wait_for_idle(
            apps=[APPLICATION_NAME, DATABASE_APP_NAME], status="active"
        )

        database_units = ops_test.model.applications[DATABASE_APP_NAME].units
        address = await get_unit_address(ops_test, database_units[0].name)
        password = await get_password(ops_test)

        # Validate the 'operator' user can still access the instance
        await execute_query_on_unit(address, password, "SELECT VERSION();")
