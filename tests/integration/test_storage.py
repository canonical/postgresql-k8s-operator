#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import os

import pytest
from pytest_operator.plugin import OpsTest

from .helpers import (
    DATABASE_APP_NAME,
    STORAGE_PATH,
    build_and_deploy,
    get_leader_unit,
)

logger = logging.getLogger(__name__)

MAX_RETRIES = 20
INSUFFICIENT_SIZE_WARNING = "<10% free space on pgdata volume."


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_filling_and_emptying_pgdata_storage(ops_test: OpsTest):
    """Build and deploy the charm and saturate its pgdata volume."""
    # Build and deploy the PostgreSQL charm.
    async with ops_test.fast_forward():
        await build_and_deploy(ops_test, 1)

    # Saturate pgdata storage with random data
    statvfs = os.statvfs(f"{STORAGE_PATH}/pgdata")
    free_space = statvfs.f_bsize * statvfs.f_bfree
    random_file_path = os.path.join(f"{STORAGE_PATH}/pgdata", "randomfile")
    with open(random_file_path, "wb") as f:
        f.write(os.urandom(free_space * 0.92))

    # wait for charm to get blocked
    async with ops_test.fast_forward():
        await ops_test.model.block_until(
            lambda: any(
                unit.workload_status == "blocked"
                for unit in ops_test.model.applications[DATABASE_APP_NAME].units
            ),
            timeout=500,
        )

    leader_unit = await get_leader_unit(ops_test, DATABASE_APP_NAME)
    assert leader_unit.workload_status == "blocked"
    assert leader_unit.workload_status_message == INSUFFICIENT_SIZE_WARNING

    # Delete big file to release storage space
    os.remove(random_file_path)

    # wait for charm to resolve
    await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1000)
