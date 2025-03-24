# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import (
    MagicMock,
    PropertyMock,
    patch,
)

import pytest
from charms.glauth_k8s.v0.ldap import LdapProviderData
from ops.testing import Harness

from charm import PostgresqlOperatorCharm
from constants import PEER


@pytest.fixture(autouse=True)
def harness():
    harness = Harness(PostgresqlOperatorCharm)

    # Set up the initial relation and hooks.
    peer_relation_id = harness.add_relation(PEER, "postgresql-k8s")
    harness.add_relation_unit(peer_relation_id, "postgresql-k8s/0")
    harness.set_leader(True)

    harness.begin()
    yield harness
    harness.cleanup()


def test_on_ldap_ready_with_certificate(harness):
    mock_event = MagicMock()

    with (
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.PostgreSQLLDAP.ca_transferred", new_callable=PropertyMock) as _ca_transferred,
    ):
        _ca_transferred.return_value = True
        harness.charm.ldap._on_ldap_ready(mock_event)
        _update_config.assert_called_once()

        peer_rel_id = harness.model.get_relation(PEER).id
        app_databag = harness.get_relation_data(peer_rel_id, harness.charm.app)
        assert "ldap_enabled" in app_databag


def test_on_ldap_ready_without_certificate(harness):
    mock_event = MagicMock()

    with (
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.PostgreSQLLDAP.ca_transferred", new_callable=PropertyMock) as _ca_transferred,
    ):
        _ca_transferred.return_value = False
        harness.charm.ldap._on_ldap_ready(mock_event)
        _update_config.assert_not_called()

        peer_rel_id = harness.model.get_relation(PEER).id
        app_databag = harness.get_relation_data(peer_rel_id, harness.charm.app)
        assert "ldap_enabled" not in app_databag


def test_on_ldap_unavailable(harness):
    mock_event = MagicMock()

    with patch("charm.PostgresqlOperatorCharm.update_config") as _update_config:
        harness.charm.ldap._on_ldap_unavailable(mock_event)
        _update_config.assert_called_once()

        peer_rel_id = harness.model.get_relation(PEER).id
        app_databag = harness.get_relation_data(peer_rel_id, harness.charm.app)
        assert app_databag["ldap_enabled"] == "False"


def test_get_relation_data(harness):
    mock_data = LdapProviderData(
        auth_method="simple",
        base_dn="dc=example,dc=net",
        bind_dn="cn=serviceuser,dc=example,dc=net",
        bind_password="password",
        bind_password_secret=None,
        starttls=False,
        ldaps_urls=[],
        urls=[],
    )

    mock_data_dict = mock_data.model_dump(exclude_none=True)
    mock_data_dict["bind_password"] = mock_data.bind_password

    assert harness.charm.ldap.get_relation_data() is None

    with harness.hooks_disabled():
        ldap_relation_id = harness.add_relation("ldap", "glauth-k8s")
        harness.update_relation_data(ldap_relation_id, "glauth-k8s", mock_data_dict)

    assert harness.charm.ldap.get_relation_data() == mock_data
