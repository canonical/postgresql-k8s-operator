#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Real-deploy integration test for the postgres terraform module.

Applies the module against the pre-created ``testing`` model (provided by the
spread/concierge substrate via the ``juju`` fixture) and waits for the deployed
``postgresql-k8s`` application to reach active/idle. Deploys the published
charm via the module's default channel; tests the module wiring, not a
locally-packed charm.
"""

import os
import shutil
import subprocess
from pathlib import Path

import jubilant
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
TERRAFORM_MODULE = REPO_ROOT / "terraform"
APP = "postgresql-k8s"
TIMEOUT = 20 * 60
# `terraform apply` blocks until the charm's units are created, so give it the deploy budget.
TF_TIMEOUT = 15 * 60
TF_BINARY = os.getenv("TF_BINARY") or "terraform"


def _arch() -> str:
    # Juju's arch name (amd64/arm64) for the runner, so the module deploys for this host's
    # architecture instead of its hardcoded `arch=amd64` default (unschedulable on arm64).
    return subprocess.run(
        ["dpkg", "--print-architecture"], capture_output=True, text=True, check=True
    ).stdout.strip()


def _run_terraform(cwd: Path, timeout: int, *args: str) -> None:
    # Bound each call so a stall fails instead of hanging to the spread kill-timeout, and
    # stream output (no capture) so progress and any stall are visible live in the log.
    subprocess.run([TF_BINARY, *args], cwd=str(cwd), check=True, timeout=timeout)


def test_terraform_apply_deploys_postgresql(juju: jubilant.Juju) -> None:
    """The terraform module must apply postgresql-k8s into the model and reach active/idle."""
    if shutil.which(TF_BINARY) is None:
        pytest.skip(f"{TF_BINARY} not found on PATH")

    model_uuid = juju.show_model().model_uuid

    _run_terraform(TERRAFORM_MODULE, TF_TIMEOUT, "init", "-input=false")
    _run_terraform(
        TERRAFORM_MODULE,
        TF_TIMEOUT,
        "apply",
        "-auto-approve",
        "-input=false",
        "-var",
        f"juju_model={model_uuid}",
        "-var",
        f"constraints=arch={_arch()}",
    )

    juju.wait(
        lambda status: jubilant.all_active(status, APP) and jubilant.all_agents_idle(status, APP),
        error=lambda status: jubilant.any_error(status, APP),
        timeout=TIMEOUT,
    )
