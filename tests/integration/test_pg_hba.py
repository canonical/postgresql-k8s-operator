#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio
import logging

import pytest
from pytest_operator.plugin import OpsTest

from .helpers import (
    DATABASE_APP_NAME,
    build_and_deploy,
    execute_query_on_unit,
    get_password,
    get_unit_address,
)

logger = logging.getLogger(__name__)

DATA_INTEGRATOR_APP_NAME = "data-integrator"


@pytest.mark.abort_on_fail
async def test_pg_hba(ops_test: OpsTest, charm):
    async with ops_test.fast_forward():
        logger.info("Deploying charms")
        await asyncio.gather(
            build_and_deploy(ops_test, charm, num_units=2, wait_for_idle=True),
            ops_test.model.deploy(
                DATA_INTEGRATOR_APP_NAME, config={"database-name": "test", "extra-user-roles": "SUPERUSER"}
            )
        )

        logger.info("Adding relation between charms")
        await ops_test.model.add_relation(DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME)
        await ops_test.model.wait_for_idle(
            apps=[DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME], status="active"
        )

        database_units = ops_test.model.applications[DATABASE_APP_NAME].units
        address = await get_unit_address(ops_test, database_units[0].name)
        password = await get_password(ops_test)
        await execute_query_on_unit(address, password, "SELECT VERSION();")
