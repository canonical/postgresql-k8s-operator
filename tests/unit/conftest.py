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
