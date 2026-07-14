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
TF_BINARY = os.getenv("TF_BINARY") or "terraform"


def _run_terraform(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [TF_BINARY, *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def test_terraform_apply_deploys_postgresql(juju: jubilant.Juju) -> None:
    """The terraform module must apply postgresql-k8s into the model and reach active/idle."""
    if shutil.which(TF_BINARY) is None:
        pytest.skip(f"{TF_BINARY} not found on PATH")

    model_uuid = juju.show_model().model_uuid

    init = _run_terraform(TERRAFORM_MODULE, "init", "-input=false")
    assert init.returncode == 0, f"terraform init failed:\n{init.stderr}{init.stdout}"

    apply = _run_terraform(
        TERRAFORM_MODULE,
        "apply",
        "-auto-approve",
        "-input=false",
        "-var",
        f"juju_model={model_uuid}",
    )
    assert apply.returncode == 0, f"terraform apply failed:\n{apply.stderr}{apply.stdout}"

    juju.wait(
        lambda status: jubilant.all_active(status, APP) and jubilant.all_agents_idle(status, APP),
        error=lambda status: jubilant.any_error(status, APP),
        timeout=TIMEOUT,
    )
