# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
from unittest.mock import PropertyMock

import pytest


# This causes every test defined in this file to run 2 times, each with
# charm.JujuVersion.has_secrets set as True or as False
@pytest.fixture(params=[True, False], autouse=True)
def juju_has_secrets(request, monkeypatch):
    monkeypatch.setattr("charm.JujuVersion.has_secrets", PropertyMock(return_value=request.param))
    return request.param


@pytest.fixture
def only_with_juju_secrets(juju_has_secrets):
    """Pretty way to skip Juju 3 tests."""
    if not juju_has_secrets:
        pytest.skip("Secrets test only applies on Juju 3.x")


@pytest.fixture
def only_without_juju_secrets(juju_has_secrets):
    """Pretty way to skip Juju 2-specific tests.

    Typically: to save CI time, when the same check were executed in a Juju 3-specific way already
    """
    if juju_has_secrets:
        pytest.skip("Skipping legacy secrets tests")
