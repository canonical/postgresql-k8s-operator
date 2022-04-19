#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import shutil

import pytest as pytest


@pytest.fixture(scope="module", autouse=True)
def copy_postgresql_library_into_tester_charm(ops_test):
    """Ensure that the tester charm uses the current PostgreSQL library."""
    library_path = "lib/charms/postgresql/v0/postgresql.py"
    install_path = "tests/integration/postgresql-tester/" + library_path
    shutil.copyfile(library_path, install_path)


@pytest.fixture(scope="module")
async def postgresql_charm(ops_test):
    """The PostgreSQL charm used for integration testing."""
    charm = await ops_test.build_charm(".")
    return charm


@pytest.fixture(scope="module")
async def postgresql_tester_charm(ops_test):
    """A charm to integration test the PostgreSQL charm."""
    charm_path = "tests/integration/postgresql-tester"
    charm = await ops_test.build_charm(charm_path)
    return charm
