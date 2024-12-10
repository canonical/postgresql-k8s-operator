# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import patch

import pytest
from ops.testing import Harness

from charm import PostgresqlOperatorCharm


@pytest.fixture(autouse=True)
def harness():
    with patch("charm.KubernetesServicePatch", lambda x, y: None):
        harness = Harness(PostgresqlOperatorCharm)

        # Set up the initial relation and hooks.
        harness.set_leader(True)
        harness.begin()

        yield harness
        harness.cleanup()


def test_on_ldap_ready(harness):
    """ """
    with patch("charm.PostgresqlOperatorCharm.update_config") as _update_config:
        _update_config.assert_called_once()


def test_on_ldap_unavailable(harness):
    """ """
    with patch("charm.PostgresqlOperatorCharm.update_config") as _update_config:
        _update_config.assert_called_once()


def test_get_ldap_information(harness):
    """ """
    pass
