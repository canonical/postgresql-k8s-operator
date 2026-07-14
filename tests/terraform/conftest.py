# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import os
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TERRAFORM_MODULE = REPO_ROOT / "terraform"
TF_BINARY = os.getenv("TF_BINARY") or "terraform"


@pytest.fixture(scope="session")
def terraform_module() -> Path:
    """The repo's terraform module directory, consumed by composition fixtures."""
    return TERRAFORM_MODULE


@pytest.fixture(scope="session")
def terraform_bin() -> str:
    """Path to the terraform binary (override with ``TF_BINARY``); skips the test if absent."""
    binary = shutil.which(TF_BINARY)
    if binary is None:
        pytest.skip(f"{TF_BINARY} not found on PATH")
    return binary
