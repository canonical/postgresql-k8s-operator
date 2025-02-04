#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import pytest
from pytest_operator.plugin import OpsTest

from .helpers import build_charm


@pytest.fixture(scope="module")
async def database_charm(ops_test: OpsTest):
    """Build the database charm."""
    charm = await build_charm(".")
    return charm
