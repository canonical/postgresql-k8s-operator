#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import time

import pytest
from markers import amd64_only, arm64_only
from pytest_operator.plugin import OpsTest

from .helpers import (
    CHARM_SERIES,
    METADATA,
)

logger = logging.getLogger(__name__)

APP_NAME = METADATA["name"]


@pytest.mark.group(1)
@amd64_only
async def test_wrong_arch_amd(ops_test: OpsTest) -> None:
    """Tries deploying an arm64 charm on amd64 host."""
    # building arm64 charm
    charm = await ops_test.build_charm(".", 1)
    resources = {
        "postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"],
    }
    await ops_test.model.deploy(
        charm,
        resources=resources,
        application_name=APP_NAME,
        trust=True,
        num_units=1,
        series=CHARM_SERIES,
        config={"profile": "testing"},
    )
    time.sleep(10)
    await ops_test.model.block_until(
        lambda: all(
            unit.workload_status == "blocked"
            for unit in ops_test.model.applications[APP_NAME].units
        ),
        timeout=60,
    )


@pytest.mark.group(1)
@arm64_only
async def test_wrong_arch_arm(ops_test: OpsTest) -> None:
    """Tries deploying an amd64 charm on arm64 host."""
    # building arm64 charm
    charm = await ops_test.build_charm(".", 0)
    resources = {
        "postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"],
    }
    await ops_test.model.deploy(
        charm,
        resources=resources,
        application_name=APP_NAME,
        trust=True,
        num_units=1,
        series=CHARM_SERIES,
        config={"profile": "testing"},
    )
    time.sleep(10)
    await ops_test.model.block_until(
        lambda: all(
            unit.workload_status == "blocked"
            for unit in ops_test.model.applications[APP_NAME].units
        ),
        timeout=60,
    )
