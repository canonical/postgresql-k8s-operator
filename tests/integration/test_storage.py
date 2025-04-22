#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest
from pytest_operator.plugin import OpsTest

from . import markers
from .helpers import (
    DATABASE_APP_NAME,
    STORAGE_PATH,
    build_and_deploy,
    get_primary,
    run_command_on_unit,
)

logger = logging.getLogger(__name__)

INSUFFICIENT_SIZE_WARNING = "<10% free space on data volume."


@markers.amd64_only
@pytest.mark.abort_on_fail
async def test_filling_and_emptying_pgdata_storage(ops_test: OpsTest, charm):
    """Build and deploy the charm and saturate its pgdata volume."""
    # Build and deploy the PostgreSQL charm.
    async with ops_test.fast_forward():
        await build_and_deploy(ops_test, charm, 1)

    # Saturate pgdata storage with random data
    primary = await get_primary(ops_test, DATABASE_APP_NAME)
    await run_command_on_unit(
        ops_test,
        primary,
        f"FREE_SPACE=$(df --output=avail {STORAGE_PATH} | tail -1) && dd if=/dev/urandom of={STORAGE_PATH}/pgdata/tmp bs=1M count=$(( (FREE_SPACE * 91 / 100) / 1024 ))",
    )

    # wait for charm to get blocked
    async with ops_test.fast_forward():
        await ops_test.model.block_until(
            lambda: any(
                unit.workload_status == "blocked"
                and unit.workload_status_message == INSUFFICIENT_SIZE_WARNING
                for unit in ops_test.model.applications[DATABASE_APP_NAME].units
            ),
            timeout=500,
        )

    # Delete big file to release storage space
    await run_command_on_unit(ops_test, primary, f"rm {STORAGE_PATH}/pgdata/tmp")

    # wait for charm to resolve
    await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1000)
