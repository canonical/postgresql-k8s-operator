#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import os
import pathlib
import time
import typing

import pytest
import yaml
from pytest_operator.plugin import OpsTest

from . import markers
from .helpers import CHARM_SERIES, DATABASE_APP_NAME, METADATA

logger = logging.getLogger(__name__)


async def fetch_charm(
    charm_path: typing.Union[str, os.PathLike],
    architecture: str,
    bases_index: int,
) -> pathlib.Path:
    charm_path = pathlib.Path(charm_path)
    charmcraft_yaml = yaml.safe_load((charm_path / "charmcraft.yaml").read_text())
    assert charmcraft_yaml["type"] == "charm"
    base = charmcraft_yaml["bases"][bases_index]
    build_on = base.get("build-on", [base])[0]
    version = build_on["channel"]
    packed_charms = list(charm_path.glob(f"*{version}-{architecture}.charm"))
    return packed_charms[0].resolve(strict=True)


@pytest.mark.group(1)
@markers.amd64_only
async def test_wrong_arch_amd(ops_test: OpsTest) -> None:
    """Tries deploying an arm64 charm on amd64 host."""
    # building arm64 charm
    charm = await fetch_charm(".", "arm64", 1)
    resources = {
        "postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"],
    }
    await ops_test.model.deploy(
        charm,
        resources=resources,
        application_name=DATABASE_APP_NAME,
        trust=True,
        num_units=1,
        series=CHARM_SERIES,
        config={"profile": "testing"},
    )
    time.sleep(10)
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME], raise_on_error=False, status="blocked"
    )


@pytest.mark.group(1)
@markers.arm64_only
async def test_wrong_arch_arm(ops_test: OpsTest) -> None:
    """Tries deploying an amd64 charm on arm64 host."""
    # building arm64 charm
    charm = await fetch_charm(".", "amd64", 0)
    resources = {
        "postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"],
    }
    await ops_test.model.deploy(
        charm,
        resources=resources,
        application_name=DATABASE_APP_NAME,
        trust=True,
        num_units=1,
        series=CHARM_SERIES,
        config={"profile": "testing"},
    )
    time.sleep(10)
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME], raise_on_error=False, status="blocked"
    )
