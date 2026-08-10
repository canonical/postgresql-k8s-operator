#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Real-deploy integration test for the postgres terraform module.

Applies the module into the pre-created ``testing`` model and waits for
active/idle. With ``TF_PROVIDER_CONSTRAINT`` set (e.g. ``~> 1.0``) it deploys
from a consumer root pinning that juju provider constraint, so the module is
exercised under the v1 line as well as its default (v2).
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import jubilant
import pytest

from .. import architecture

_JUJU_PROVIDER = "registry.terraform.io/juju/juju"

REPO_ROOT = Path(__file__).resolve().parents[3]
TERRAFORM_MODULE = REPO_ROOT / "terraform"
APP = "postgresql-k8s"
TIMEOUT = 20 * 60
# `terraform apply` blocks until the charm's units are created, so give it the deploy budget.
TF_TIMEOUT = 15 * 60
TF_BINARY = os.getenv("TF_BINARY") or "terraform"
# When set (e.g. "~> 1.0"), deploy from a consumer root that pins the juju provider to that
# constraint instead of applying the module directly; unset applies the module as-is (v2.x).
PROVIDER_CONSTRAINT = os.getenv("TF_PROVIDER_CONSTRAINT")
# Storage directives for the postgresql-k8s charm: archive, data, logs, temp.
STORAGE_DIRECTIVES = '{"data"="2G","archive"="1G","logs"="1G","temp"="512M"}'
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


def _build_consumer_root(root: Path, module: Path, constraint: str) -> None:
    # A downstream root that sources the module and pins the juju provider via a sibling
    # constraint, so init resolves the major the consumer asked for. Forwards the deploy vars.
    root.mkdir(parents=True, exist_ok=True)
    (root / "versions.tf").write_text(
        "terraform {\n"
        '  required_version = ">= 1.6.6"\n'
        "  required_providers {\n"
        "    juju = {\n"
        '      source  = "juju/juju"\n'
        f'      version = "{constraint}"\n'
        "    }\n"
        "  }\n"
        "}\n"
    )
    (root / "main.tf").write_text(
        'variable "juju_model" { type = string }\n'
        'variable "constraints" { type = string }\n'
        'variable "storage_directives" { type = map(string) }\n'
        'variable "config" { type = map(string) }\n'
        'module "postgres" {\n'
        f'  source             = "{module}/"\n'
        "  juju_model         = var.juju_model\n"
        "  constraints        = var.constraints\n"
        "  storage_directives = var.storage_directives\n"
        "  config             = var.config\n"
        "}\n"
        'output "application_name" {\n'
        "  value = module.postgres.application_name\n"
        "}\n"
    )


def test_terraform_apply_deploys_postgresql(juju: jubilant.Juju, tmp_path: Path) -> None:
    """The module must apply postgresql-k8s with storage/config, reach active/idle, and expose outputs."""
    if shutil.which(TF_BINARY) is None:
        pytest.skip(f"{TF_BINARY} not found on PATH")

    model_uuid = juju.show_model().model_uuid

    # Apply the module directly (default, v2.x) or from a consumer root pinning the provider
    # major via TF_PROVIDER_CONSTRAINT — same `-var` strings drive both paths.
    deploy_dir = tmp_path / "consumer" if PROVIDER_CONSTRAINT else TERRAFORM_MODULE
    if PROVIDER_CONSTRAINT:
        _build_consumer_root(deploy_dir, TERRAFORM_MODULE, PROVIDER_CONSTRAINT)

    _run_terraform(deploy_dir, TF_TIMEOUT, "init", "-input=false")

    # Guard against the v1 leg silently degrading to a second v2 run (e.g. if
    # TF_PROVIDER_CONSTRAINT stopped reaching the test): assert init actually resolved the
    # juju provider major this leg intends — 1 when pinned to `~> 1.0`, else the module's own.
    versions = _run_terraform(deploy_dir, TF_TIMEOUT, "version", "-json", capture=True)
    resolved = json.loads(versions.stdout)["provider_selections"][_JUJU_PROVIDER]
    expected_major = "1" if PROVIDER_CONSTRAINT == "~> 1.0" else "2"
    assert resolved.split(".")[0] == expected_major, (
        f"expected juju provider major {expected_major}, resolved {resolved}"
    )

    _run_terraform(
        deploy_dir,
        TF_TIMEOUT,
        "apply",
        "-auto-approve",
        "-input=false",
        "-var",
        f"juju_model={model_uuid}",
        # Deploy for the runner's arch, not the module's hardcoded `arch=amd64` (unschedulable on arm64).
        "-var",
        f"constraints=arch={architecture.architecture}",
        "-var",
        f"storage_directives={STORAGE_DIRECTIVES}",
        "-var",
        f"config={CONFIG}",
    )

    juju.wait(
        lambda status: jubilant.all_active(status, APP) and jubilant.all_agents_idle(status, APP),
        error=lambda status: jubilant.any_error(status, APP),
        timeout=TIMEOUT,
    )

    # The module exposes an `application_name` output; assert it reflects the deployed app.
    # capture=True so `.stdout` holds the value instead of streaming to the log.
    output = _run_terraform(
        deploy_dir, TF_TIMEOUT, "output", "-raw", "application_name", capture=True
    )
    assert output.stdout.strip() == APP, f"application_name output: {output.stdout!r}"
