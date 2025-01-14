#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import os
import pathlib
import typing

import pytest
from pytest_operator.plugin import OpsTest

from . import markers
from .helpers import CHARM_BASE, DATABASE_APP_NAME, METADATA

logger = logging.getLogger(__name__)


async def fetch_charm(
    charm_path: typing.Union[str, os.PathLike],
    architecture: str,
) -> pathlib.Path:
    """Fetches packed charm from CI runner without checking for architecture."""
    charm_path = pathlib.Path(charm_path)
    packed_charms = list(charm_path.glob(f"*-{architecture}.charm"))
    return packed_charms[0].resolve(strict=True)


@pytest.mark.group(1)
@markers.amd64_only
async def test_arm_charm_on_amd_host(ops_test: OpsTest) -> None:
    """Tries deploying an arm64 charm on amd64 host."""
    charm = await fetch_charm(".", "arm64")
    resources = {
        "postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"],
    }
    await ops_test.model.deploy(
        charm,
        resources=resources,
        application_name=DATABASE_APP_NAME,
        trust=True,
        num_units=1,
        base=CHARM_BASE,
        config={"profile": "testing"},
    )

    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME], raise_on_error=False, status="blocked"
    )


@pytest.mark.group(1)
@markers.arm64_only
async def test_amd_charm_on_arm_host(ops_test: OpsTest) -> None:
    """Tries deploying an amd64 charm on arm64 host."""
    charm = await fetch_charm(".", "amd64")
    resources = {
        "postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"],
    }
    await ops_test.model.deploy(
        charm,
        resources=resources,
        application_name=DATABASE_APP_NAME,
        trust=True,
        num_units=1,
        base=CHARM_BASE,
        config={"profile": "testing"},
    )

    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME], raise_on_error=False, status="blocked"
    )
