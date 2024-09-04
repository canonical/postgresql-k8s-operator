#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest
from pytest_operator.plugin import OpsTest

from .helpers import (
    DATABASE_APP_NAME,
    build_and_deploy,
    db_connect,
    get_leader_unit,
    get_password,
    get_primary,
    get_unit_address,
)

logger = logging.getLogger(__name__)

APP_NAME = "untrusted-postgresql-k8s"
MAX_RETRIES = 20
INSUFFICIENT_SIZE_WARNING = "<10%% free space on pgdata volume."


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_filling_and_emptying_pgdata_storage(ops_test: OpsTest):
    """Build and deploy the charm and saturate its pgdata volume."""
    # Build and deploy the PostgreSQL charm.
    async with ops_test.fast_forward():
        await build_and_deploy(ops_test, 1)

    # Write some data to the initial primary (this causes a divergence
    # in the instances' timelines).
    primary = await get_primary(ops_test)
    host = await get_unit_address(ops_test, primary)
    password = await get_password(ops_test)
    with db_connect(host, password) as connection:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE big_table (testcol INT);")
            cursor.execute("INSERT INTO big_table SELECT generate_series(1,237500000);")
    connection.close()

    async with ops_test.fast_forward():
        await ops_test.model.block_until(
            lambda: any(
                unit.workload_status == "blocked"
                for unit in ops_test.model.applications[DATABASE_APP_NAME].units
            ),
            timeout=500,
        )

    leader_unit = await get_leader_unit(ops_test, APP_NAME)
    assert leader_unit.workload_status == "blocked"
    assert leader_unit.workload_status_message == INSUFFICIENT_SIZE_WARNING
