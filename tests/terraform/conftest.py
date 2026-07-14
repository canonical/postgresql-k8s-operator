# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TERRAFORM_MODULE = REPO_ROOT / "terraform"


@pytest.fixture(scope="session")
def terraform_module() -> Path:
    """The repo's terraform module directory, consumed by composition fixtures."""
    return TERRAFORM_MODULE


@pytest.fixture(scope="session")
def terraform_bin() -> str:
    """Path to the terraform binary; skips the test if absent."""
    binary = shutil.which("terraform")
    if binary is None:
        pytest.skip("terraform binary not found on PATH")
    return binary
