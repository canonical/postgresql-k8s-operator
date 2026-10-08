#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Real-deploy integration test for the postgresql-k8s terraform module.

Applies the module into the pre-created ``testing`` model and waits for
active/idle. The module pins the juju provider to the v1 line, so there is a
single deploy leg; the resolved provider major is asserted before applying.

CI runs this module twice: once against the charm from Charmhub, and once
(``TERRAFORM_CHARM_SOURCE=local``) also refreshing to the locally packed charm.
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from pytest_operator.plugin import OpsTest

from .. import architecture
from ..helpers import METADATA, get_leader_unit

logger = logging.getLogger(__name__)

_JUJU_PROVIDER = "registry.terraform.io/juju/juju"
# Major admitted by the module's `required_providers` constraint (`~> 1.0`). Kept in sync with
# tests/terraform/test_compositions.py, which asserts the same cap statically.
EXPECTED_PROVIDER_MAJOR = "1"

REPO_ROOT = Path(__file__).resolve().parents[3]
TERRAFORM_MODULE = REPO_ROOT / "terraform"
# The module's default `app_name`.
APP = "postgresql-k8s"
TIMEOUT = 20 * 60
# `terraform apply` blocks until the application is created, so give it the deploy budget.
TF_TIMEOUT = 15 * 60
TF_BINARY = os.getenv("TF_BINARY") or "terraform"
# `charmhub`: test the module against the charm it deploys from Charmhub.
# `local`: additionally refresh the deployed application to the locally packed charm.
CHARM_SOURCE = os.getenv("TERRAFORM_CHARM_SOURCE") or "charmhub"
# Storage directive for the postgresql-k8s charm's pgdata storage — drives the
# `storage_directives` variable.
STORAGE_DIRECTIVES = '{"pgdata"="2G"}'
# A string-typed postgresql-k8s config option (profile) — drives the `config` variable.
CONFIG = '{"profile"="testing"}'


def _run_terraform(
    cwd: Path, timeout: int, *args: str, capture: bool = False
) -> subprocess.CompletedProcess:
    # Stream by default so the slow init/apply show live progress; capture only when the
    # caller reads stdout (else `.stdout` is None). Timeout so a stall fails fast.
    return subprocess.run(
        [TF_BINARY, *args],
        cwd=str(cwd),
        check=True,
        timeout=timeout,
        capture_output=capture,
        text=capture,
    )


async def _terraform(*args: str, capture: bool = False) -> subprocess.CompletedProcess:
    # Run in a thread: terraform can block for minutes, which would otherwise starve the event
    # loop that keeps the python-libjuju connection alive.
    return await asyncio.to_thread(
        _run_terraform, TERRAFORM_MODULE, TF_TIMEOUT, *args, capture=capture
    )


@pytest.mark.abort_on_fail
async def test_terraform_apply_deploys_postgresql(ops_test: OpsTest) -> None:
    """The module must deploy the charm with storage/config, become active, and expose outputs."""
    if shutil.which(TF_BINARY) is None:
        pytest.skip(f"{TF_BINARY} not found on PATH")

    model_uuid = ops_test.model.info.uuid

    await _terraform("init", "-input=false")

    # Guard against the module's provider pin drifting unnoticed: assert init resolved the
    # juju provider major the module's `required_providers` constraint admits.
    versions = await _terraform("version", "-json", capture=True)
    resolved = json.loads(versions.stdout)["provider_selections"][_JUJU_PROVIDER]
    assert resolved.split(".")[0] == EXPECTED_PROVIDER_MAJOR, (
        f"expected juju provider major {EXPECTED_PROVIDER_MAJOR}, resolved {resolved}"
    )

    await _terraform(
        "apply",
        "-auto-approve",
        "-input=false",
        "-var",
        f"model_uuid={model_uuid}",
        # Deploy for the runner's arch; the module sets no arch constraint by default.
        "-var",
        f"constraints=arch={architecture.architecture}",
        "-var",
        f"storage_directives={STORAGE_DIRECTIVES}",
        "-var",
        f"config={CONFIG}",
    )

    logger.info("Wait for the application deployed by terraform to become active")
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(apps=[APP], status="active", timeout=TIMEOUT)

    # The module exposes an `app_name` output; assert it reflects the deployed app.
    # capture=True so `.stdout` holds the value instead of streaming to the log.
    output = await _terraform("output", "-raw", "app_name", capture=True)
    assert output.stdout.strip() == APP, f"app_name output: {output.stdout!r}"


@pytest.mark.skipif(CHARM_SOURCE != "local", reason="TERRAFORM_CHARM_SOURCE != local")
async def test_refresh_to_local_charm(ops_test: OpsTest, charm) -> None:
    """The application deployed by the module must refresh to the locally packed charm."""
    # The juju provider cannot deploy a local charm, so refresh the terraform-deployed
    # application (from Charmhub) to the charm packed from this branch.
    leader_unit = await get_leader_unit(ops_test, APP)
    assert leader_unit is not None, "No leader unit found"

    logger.info("Run pre-upgrade-check action")
    action = await leader_unit.run_action("pre-upgrade-check")
    await action.wait()

    resources = {"postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"]}
    application = ops_test.model.applications[APP]

    logger.info("Refresh the charm")
    await application.refresh(path=charm, resources=resources)

    async with ops_test.fast_forward("60s"):
        # Check the charm origin so the wait cannot succeed before the refresh has started.
        await ops_test.model.block_until(
            lambda: application.charm_url.startswith("local:"), timeout=TIMEOUT
        )
        await ops_test.model.wait_for_idle(
            apps=[APP], status="active", idle_period=30, timeout=TIMEOUT
        )
