#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for PostgreSQL symlinks (pgdata)."""

import logging

import pytest
from pytest_operator.plugin import OpsTest

from .helpers import (
    METADATA,
    PGDATA_PATH,
    build_and_deploy,
    run_command_on_unit,
)

logger = logging.getLogger(__name__)

APP_NAME = METADATA["name"]
UNIT_IDS = [0, 1, 2]
PGDATA_SYMLINK_PATH = "/var/lib/postgresql/16/main"


@pytest.mark.abort_on_fail
@pytest.mark.skip_if_deployed
async def test_build_and_deploy(ops_test: OpsTest, charm):
    """Build the charm-under-test and deploy it.

    Assert on the unit status before any relations/configurations take place.
    """
    async with ops_test.fast_forward():
        await build_and_deploy(ops_test, charm, len(UNIT_IDS), APP_NAME)
    for unit_id in UNIT_IDS:
        assert ops_test.model.applications[APP_NAME].units[unit_id].workload_status == "active"


@pytest.mark.parametrize("unit_id", UNIT_IDS)
async def test_pgdata_symlinks(ops_test: OpsTest, unit_id: int):
    """Test that symlink for pgdata is correctly created."""
    unit_name = f"{APP_NAME}/{unit_id}"

    # Check pgdata symlink exists and points to correct location
    pgdata_symlink_check = await run_command_on_unit(
        ops_test, unit_name, f"readlink -f {PGDATA_SYMLINK_PATH}"
    )
    assert pgdata_symlink_check.strip() == PGDATA_PATH, (
        f"Expected pgdata symlink to point to {PGDATA_PATH}, got {pgdata_symlink_check.strip()}"
    )

    # Verify symlink is owned by postgres:postgres
    pgdata_owner = await run_command_on_unit(
        ops_test, unit_name, f"stat -c '%U:%G' {PGDATA_SYMLINK_PATH}"
    )
    assert pgdata_owner.strip() == "postgres:postgres", (
        f"Expected pgdata symlink to be owned by postgres:postgres, got {pgdata_owner.strip()}"
    )
