# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
from unittest.mock import Mock, PropertyMock, patch

import pytest
from charms.tempo_coordinator_k8s.v0.charm_tracing import charm_tracing_disabled


# This causes every test defined in this file to run 2 times, each with
# charm.JujuVersion.has_secrets set as True or as False
@pytest.fixture(autouse=True)
def juju_has_secrets(request, monkeypatch):
    monkeypatch.setattr("ops.JujuVersion.has_secrets", PropertyMock(return_value=True))


@pytest.fixture(autouse=True)
def disable_charm_tracing():
    with charm_tracing_disabled():
        yield


# class _MockRefresh:
#     in_progress = False
#     next_unit_allowed_to_refresh = True
#     workload_allowed_to_start = True
#     app_status_higher_priority = None
#     unit_status_higher_priority = None
#
#     def __init__(self, _, /):
#         pass
#
#     def unit_status_lower_priority(self, *, workload_is_running=True):
#         return None


# @pytest.fixture(autouse=True)
# def patch(monkeypatch):
#     monkeypatch.setattr("charm_refresh.Kubernetes", _MockRefresh)


@pytest.fixture(autouse=True)
def mock_refresh():
    """Fixture to shunt refresh logic and events."""
    refresh_mock = Mock()
    refresh_mock.in_progress = False
    refresh_mock.unit_status_higher_priority = None
    refresh_mock.unit_status_lower_priority.return_value = None
    refresh_mock.next_unit_allowed_to_refresh = True
    refresh_mock.workload_allowed_to_start = True

    # Mock the _RefreshVersions class to avoid KeyError when charm key is missing
    versions_mock = Mock()
    versions_mock.charm = "v1/4.0.0"
    versions_mock.workload = "4.0.0"

    with (
        patch("charm_refresh.Kubernetes", Mock(return_value=refresh_mock)),
        patch("charm.PostgreSQLRefresh", Mock(return_value=None)),
        patch("charm_refresh._main._RefreshVersions", Mock(return_value=versions_mock)),
    ):
        yield
