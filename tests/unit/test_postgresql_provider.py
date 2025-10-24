# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import Mock, PropertyMock, patch, sentinel

import pytest
from ops import Unit
from ops.framework import EventBase
from ops.model import ActiveStatus, BlockedStatus
from ops.testing import Harness
from single_kernel_postgresql.utils.postgresql import (
    ACCESS_GROUP_RELATION,
    PostgreSQLCreateDatabaseError,
    PostgreSQLCreateUserError,
    PostgreSQLGetPostgreSQLVersionError,
)

from charm import PostgresqlOperatorCharm
from constants import PEER

DATABASE = "test_database"
EXTRA_USER_ROLES = "CREATEDB,CREATEROLE"
RELATION_NAME = "database"
POSTGRESQL_VERSION = "16"


@pytest.fixture(autouse=True)
def harness():
    harness = Harness(PostgresqlOperatorCharm)

    # Set up the initial relation and hooks.
    harness.set_leader(True)
    harness.begin()

    # Define some relations.
    rel_id = harness.add_relation(RELATION_NAME, "application")
    harness.add_relation_unit(rel_id, "application/0")
    peer_rel_id = harness.add_relation(PEER, harness.charm.app.name)
    harness.add_relation_unit(peer_rel_id, harness.charm.unit.name)
    harness.update_relation_data(
        peer_rel_id,
        harness.charm.app.name,
        {"cluster_initialised": "True"},
    )
    yield harness
    harness.cleanup()


def request_database(_harness):
    # Reset the charm status.
    _harness.model.unit.status = ActiveStatus()
    rel_id = _harness.model.get_relation(RELATION_NAME).id

    # Reset the application databag.
    _harness.update_relation_data(
        rel_id,
        "application",
        {"database": "", "extra-user-roles": ""},
    )

    # Reset the database databag.
    _harness.update_relation_data(
        rel_id,
        _harness.charm.app.name,
        {"data": "", "username": "", "password": "", "version": "", "database": ""},
    )

    # Simulate the request of a new database plus extra user roles.
    _harness.update_relation_data(
        rel_id,
        "application",
        {"database": DATABASE, "extra-user-roles": EXTRA_USER_ROLES},
    )


def test_on_database_requested(harness):
    with (
        patch("charm.PostgresqlOperatorCharm.update_config"),
        patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock,
        patch.object(EventBase, "defer") as _defer,
        patch(
            "charm.Patroni.primary_endpoint_ready", new_callable=PropertyMock
        ) as _primary_endpoint_ready,
        patch(
            "relations.postgresql_provider.new_password", return_value="test-password"
        ) as _new_password,
    ):
        rel_id = harness.model.get_relation(RELATION_NAME).id
        # Set some side effects to test multiple situations.
        _primary_endpoint_ready.side_effect = [False, True, True, True, True, True]
        postgresql_mock.create_user = PropertyMock(
            side_effect=[None, PostgreSQLCreateUserError, None]
        )
        postgresql_mock.create_database = PropertyMock(
            side_effect=[None, PostgreSQLCreateDatabaseError, None, None]
        )
        postgresql_mock.get_postgresql_version = PropertyMock(
            side_effect=[
                POSTGRESQL_VERSION,
                PostgreSQLGetPostgreSQLVersionError,
            ]
        )

        # Request a database before the database is ready.
        request_database(harness)
        _defer.assert_called_once()

        # Request it again when the database is ready.
        request_database(harness)

        # Assert that the correct calls were made.
        user = f"relation_id_{rel_id}"
        expected_user_roles = [role.lower() for role in EXTRA_USER_ROLES.split(",")]
        expected_user_roles.append(ACCESS_GROUP_RELATION)
        postgresql_mock.create_user.assert_called_once_with(
            user,
            "test-password",
            extra_user_roles=expected_user_roles,
            database=DATABASE,
        )
        postgresql_mock.create_database.assert_called_once_with(DATABASE, plugins=["pgaudit"])
        postgresql_mock.get_postgresql_version.assert_called_once()

        # Assert that the relation data was updated correctly.
        assert harness.get_relation_data(rel_id, harness.charm.app.name) == {
            "data": f'{{"database": "{DATABASE}", "extra-user-roles": "{EXTRA_USER_ROLES}"}}',
            "endpoints": "postgresql-k8s-primary.None.svc.cluster.local:5432",
            "username": user,
            "password": "test-password",
            "read-only-endpoints": "postgresql-k8s-replicas.None.svc.cluster.local:5432",
            "uris": f"postgresql://{user}:test-password@postgresql-k8s-primary.None.svc.cluster.local:5432/{DATABASE}",
            "version": POSTGRESQL_VERSION,
            "database": f"{DATABASE}",
            "tls": "False",
        }

        # Assert no BlockedStatus was set.
        assert not isinstance(harness.model.unit.status, BlockedStatus)

        # BlockedStatus due to a PostgreSQLCreateUserError.
        request_database(harness)
        assert isinstance(harness.model.unit.status, BlockedStatus)
        # No data is set in the databag by the database.
        assert harness.get_relation_data(rel_id, harness.charm.app.name) == {
            "data": f'{{"database": "{DATABASE}", "extra-user-roles": "{EXTRA_USER_ROLES}"}}',
            "endpoints": "postgresql-k8s-primary.None.svc.cluster.local:5432",
            "uris": f"postgresql://{user}:test-password@postgresql-k8s-primary.None.svc.cluster.local:5432/{DATABASE}",
            "read-only-endpoints": "postgresql-k8s-replicas.None.svc.cluster.local:5432",
            "tls": "False",
        }

        # BlockedStatus due to a PostgreSQLCreateDatabaseError.
        request_database(harness)
        assert isinstance(harness.model.unit.status, BlockedStatus)
        # No data is set in the databag by the database.
        assert harness.get_relation_data(rel_id, harness.charm.app.name) == {
            "data": f'{{"database": "{DATABASE}", "extra-user-roles": "{EXTRA_USER_ROLES}"}}',
            "endpoints": "postgresql-k8s-primary.None.svc.cluster.local:5432",
            "read-only-endpoints": "postgresql-k8s-replicas.None.svc.cluster.local:5432",
            "uris": f"postgresql://{user}:test-password@postgresql-k8s-primary.None.svc.cluster.local:5432/{DATABASE}",
            "tls": "False",
        }

        # BlockedStatus due to a PostgreSQLGetPostgreSQLVersionError.
        request_database(harness)
        assert isinstance(harness.model.unit.status, BlockedStatus)


def test_on_relation_departed(harness):
    with patch("charm.Patroni.member_started", new_callable=PropertyMock(return_value=True)):
        peer_rel_id = harness.model.get_relation(PEER).id
        # Test when this unit is departing the relation (due to a scale down event).
        assert "departing" not in harness.get_relation_data(peer_rel_id, harness.charm.unit)
        event = Mock()
        event.relation.data = {harness.charm.app: {}, harness.charm.unit: {}}
        event.departing_unit = harness.charm.unit
        harness.charm.postgresql_client_relation._on_relation_departed(event)
        assert "departing" in harness.get_relation_data(peer_rel_id, harness.charm.unit)

        # Test when this unit is departing the relation (due to the relation being broken between the apps).
        with harness.hooks_disabled():
            harness.update_relation_data(peer_rel_id, harness.charm.unit.name, {"departing": ""})
        event.relation.data = {harness.charm.app: {}, harness.charm.unit: {}}
        event.departing_unit = Unit(f"{harness.charm.app}/1", None, harness.charm.app._backend, {})
        harness.charm.postgresql_client_relation._on_relation_departed(event)
        relation_data = harness.get_relation_data(peer_rel_id, harness.charm.unit)
        assert "departing" not in relation_data


def test_on_relation_broken(harness):
    with harness.hooks_disabled():
        harness.set_leader()
    with (
        patch("charm.PostgresqlOperatorCharm.update_config"),
        patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock,
        patch(
            "charm.Patroni.member_started", new_callable=PropertyMock(return_value=True)
        ) as _member_started,
    ):
        rel_id = harness.model.get_relation(RELATION_NAME).id
        peer_rel_id = harness.model.get_relation(PEER).id
        # Test when this unit is departing the relation (due to the relation being broken between the apps).
        event = Mock()
        event.relation.id = rel_id
        harness.charm.postgresql_client_relation._on_relation_broken(event)
        user = f"relation_id_{rel_id}"
        postgresql_mock.delete_user.assert_called_once_with(user)

        # Test when this unit is departing the relation (due to a scale down event).
        postgresql_mock.reset_mock()
        with harness.hooks_disabled():
            harness.update_relation_data(
                peer_rel_id, harness.charm.unit.name, {"departing": "True"}
            )
        harness.charm.postgresql_client_relation._on_relation_broken(event)
        postgresql_mock.delete_user.assert_not_called()


def test_update_tls_flag(harness):
    with (
        patch("charm.TLS.get_client_tls_files", return_value=(None, sentinel.ca, None)),
        patch(
            "relations.postgresql_provider.new_password", return_value="test-password"
        ) as _new_password,
        patch(
            "relations.postgresql_provider.DatabaseProvides.fetch_relation_field",
            side_effect=[None, "db"],
        ),
        patch(
            "relations.postgresql_provider.DatabaseProvides.set_tls",
        ) as _set_tls,
        patch(
            "relations.postgresql_provider.DatabaseProvides.set_tls_ca",
        ) as _set_tls_ca,
    ):
        with harness.hooks_disabled():
            second_rel = harness.add_relation(RELATION_NAME, "second_app")
        harness.charm.postgresql_client_relation.update_tls_flag("True")
        _set_tls.assert_called_once_with(second_rel, "True")
        _set_tls_ca.assert_called_once_with(second_rel, sentinel.ca)
