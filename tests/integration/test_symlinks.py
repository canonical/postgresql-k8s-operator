#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for PostgreSQL symlinks (pgdata)."""

import logging

import jubilant
import pytest

from .helpers import (
    ACTUAL_PGDATA_PATH,
    DATABASE_APP_NAME,
    METADATA,
)

logger = logging.getLogger(__name__)

APP_NAME = DATABASE_APP_NAME
UNIT_IDS = [0, 1, 2]
PGDATA_SYMLINK_PATH = "/var/lib/postgresql/16/main"


@pytest.mark.abort_on_fail
def test_build_and_deploy(juju: jubilant.Juju, charm):
    """Build the charm-under-test and deploy it.

    Assert on the unit status before any relations/configurations take place.
    """
    if APP_NAME not in juju.status().apps:
        resources = {
            "postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"]
        }
        juju.deploy(
            charm,
            config={"profile": "testing"},
            num_units=len(UNIT_IDS),
            resources=resources,
            trust=True,
        )

    def _all_active_idle(status):
        if not jubilant.all_active(status, APP_NAME):
            return False
        for uid in UNIT_IDS:
            unit = status.apps[APP_NAME].units[f"{APP_NAME}/{uid}"]
            if unit.juju_status.current != "idle":
                return False
        return True

    juju.wait(_all_active_idle, timeout=1500)
    status = juju.status()
    for unit_id in UNIT_IDS:
        unit_name = f"{APP_NAME}/{unit_id}"
        assert status.apps[APP_NAME].units[unit_name].is_active


@pytest.mark.parametrize("unit_id", UNIT_IDS)
def test_pgdata_symlinks(juju: jubilant.Juju, unit_id: int):
    """Test that symlink for pgdata is correctly created."""
    unit_name = f"{APP_NAME}/{unit_id}"

    # Check pgdata symlink exists and points to correct location
    pgdata_symlink_check = juju.ssh(
        unit_name, "readlink", "-f", PGDATA_SYMLINK_PATH, container="postgresql"
    )
    assert pgdata_symlink_check.strip() == ACTUAL_PGDATA_PATH, (
        f"Expected pgdata symlink to point to {ACTUAL_PGDATA_PATH}, got {pgdata_symlink_check.strip()}"
    )

    # Verify symlink is owned by postgres:postgres
    pgdata_owner = juju.ssh(
        unit_name, "stat", "-c", "%U:%G", PGDATA_SYMLINK_PATH, container="postgresql"
    )
    assert pgdata_owner.strip() == "postgres:postgres", (
        f"Expected pgdata symlink to be owned by postgres:postgres, got {pgdata_owner.strip()}"
    )
