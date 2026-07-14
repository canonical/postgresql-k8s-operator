# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Static constraint-composition tests for the postgres terraform module.

Each case builds a throwaway root module that sources the repo's ``terraform/``
module and declares a sibling ``juju`` provider constraint, then runs
``terraform init`` (provider resolution against the live registry) and asserts
on the resolved provider major version (or expected failure).

This guards the module's ``required_providers`` constraint against the failure
mode where a downstream module pairs postgres with a juju provider constraint
the module's own constraint cannot satisfy.
"""

import re
import subprocess
from pathlib import Path

import pytest

_INSTALLED_RE = re.compile(r"Installed juju/juju v(\d+)\.(\d+)\.(\d+)")

# (root juju provider constraint, expect init success, resolved provider major)
CASES = [
    pytest.param("~> 1.0", True, "1", id="v1_consumer"),
    pytest.param(">= 1.1.1", True, "2", id="identity_sibling"),
    pytest.param(">= 2.0", True, "2", id="v2_consumer"),
    pytest.param("~> 2.0", True, "2", id="v2_pinned"),
    pytest.param(">= 2.9", False, None, id="no_such_version"),
    pytest.param(">= 3.0", False, None, id="beyond_cap"),
]


def _run_terraform(binary: str, cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [binary, *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def _write_root(root: Path, module: Path, constraint: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "versions.tf").write_text(
        f"terraform {{\n"
        f'  required_version = ">= 1.6.6"\n'
        f"  required_providers {{\n"
        f"    juju = {{\n"
        f'      source  = "juju/juju"\n'
        f'      version = "{constraint}"\n'
        f"    }}\n"
        f"  }}\n"
        f"}}\n"
    )
    (root / "main.tf").write_text(
        f'module "postgres" {{\n'
        f'  source     = "{module}/"\n'
        f'  juju_model = "unused-during-init"\n'
        f"}}\n"
    )


@pytest.mark.parametrize("constraint,expect_ok,expect_major", CASES)
def test_composition_resolves_provider(
    tmp_path: Path,
    terraform_module: Path,
    terraform_bin: str,
    constraint: str,
    expect_ok: bool,
    expect_major: str | None,
) -> None:
    """Assert the module's constraint composes with a sibling juju constraint."""
    root = tmp_path / "root"
    _write_root(root, terraform_module, constraint)

    init = _run_terraform(terraform_bin, root, "init", "-backend=false", "-input=false")

    if expect_ok:
        assert init.returncode == 0, f"init failed:\n{init.stderr}{init.stdout}"
        match = _INSTALLED_RE.search(init.stdout)
        assert match is not None, f"no resolved provider in output:\n{init.stdout}"
        assert match.group(1) == expect_major, (
            f"resolved {match.group(0)}, expected major {expect_major}"
        )
    else:
        assert init.returncode != 0, f"expected init to fail but it succeeded:\n{init.stdout}"
