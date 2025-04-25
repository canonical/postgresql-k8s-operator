# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import Mock, PropertyMock, patch

import pytest
from charms.postgresql_k8s.v0.postgresql import (
    ACCESS_GROUP_RELATION,
    PostgreSQLCreateDatabaseError,
    PostgreSQLCreateUserError,
    PostgreSQLGetPostgreSQLVersionError,
)
from ops import Unit
from ops.framework import EventBase
from ops.model import ActiveStatus, BlockedStatus
from ops.testing import Harness

from charm import PostgresqlOperatorCharm
from constants import DATABASE_PORT, PEER

DATABASE = "test_database"
RELATION_NAME = "db"
POSTGRESQL_VERSION = "14"


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
    harness.add_relation_unit(peer_rel_id, f"{harness.charm.app.name}/1")
    harness.add_relation_unit(peer_rel_id, harness.charm.unit.name)
    harness.update_relation_data(
        peer_rel_id,
        harness.charm.app.name,
        {"cluster_initialised": "True"},
    )
    yield harness
    harness.cleanup()


def clear_relation_data(_harness):
    data = {
        "allowed-subnets": "",
        "allowed-units": "",
        "host": "",
        "port": "",
        "master": "",
        "standbys": "",
        "version": "",
        "user": "",
        "password": "",
        "database": "",
        "extensions": "",
    }
    rel_id = _harness.model.get_relation(RELATION_NAME).id
    _harness.update_relation_data(rel_id, _harness.charm.app.name, data)
    _harness.update_relation_data(rel_id, _harness.charm.unit.name, data)


def request_database(_harness):
    # Reset the charm status.
    _harness.model.unit.status = ActiveStatus()
    rel_id = _harness.model.get_relation(RELATION_NAME).id

    with _harness.hooks_disabled():
        # Reset the application databag.
        _harness.update_relation_data(
            rel_id,
            "application/0",
            {"database": ""},
        )

        # Reset the database databag.
        clear_relation_data(_harness)

    # Simulate the request of a new database.
    _harness.update_relation_data(
        rel_id,
        "application/0",
        {"database": DATABASE},
    )


def test_on_relation_changed(harness):
    with (
        patch("charm.PostgresqlOperatorCharm.update_config"),
        patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock,
        patch("charm.DbProvides.set_up_relation") as _set_up_relation,
        patch.object(EventBase, "defer") as _defer,
        patch("charm.Patroni.member_started", new_callable=PropertyMock) as _member_started,
    ):
        peer_rel_id = harness.model.get_relation(PEER).id
        # Set some side effects to test multiple situations.
        _member_started.side_effect = [False, False, True, True]
        postgresql_mock.list_users.return_value = {"relation_id_0"}

        # Request a database before the cluster is initialised.
        request_database(harness)
        _defer.assert_called_once()
        _set_up_relation.assert_not_called()

        # Request a database before the database is ready.
        with harness.hooks_disabled():
            harness.update_relation_data(
                peer_rel_id,
                harness.charm.app.name,
                {"cluster_initialised": "True"},
            )
        request_database(harness)
        assert _defer.call_count == 2
        _set_up_relation.assert_not_called()

        # Request a database to a non leader unit.
        _defer.reset_mock()
        with harness.hooks_disabled():
            harness.set_leader(False)
        request_database(harness)
        _defer.assert_not_called()
        _set_up_relation.assert_not_called()

        # Request it again in a leader unit.
        with harness.hooks_disabled():
            harness.set_leader()
        request_database(harness)
        _defer.assert_not_called()
        _set_up_relation.assert_called_once()


def test_get_extensions(harness):
    # Test when there are no extensions in the relation databags.
    rel_id = harness.model.get_relation(RELATION_NAME).id
    relation = harness.model.get_relation(RELATION_NAME, rel_id)
    assert harness.charm.legacy_db_relation._get_extensions(relation) == ([], set())

    # Test when there are extensions in the application relation databag.
    extensions = ["", "citext:public", "debversion"]
    with harness.hooks_disabled():
        harness.update_relation_data(
            rel_id,
            "application",
            {"extensions": ",".join(extensions)},
        )
    assert harness.charm.legacy_db_relation._get_extensions(relation) == (
        [extensions[1], extensions[2]],
        {extensions[1].split(":")[0], extensions[2]},
    )

    # Test when there are extensions in the unit relation databag.
    with harness.hooks_disabled():
        harness.update_relation_data(
            rel_id,
            "application",
            {"extensions": ""},
        )
        harness.update_relation_data(
            rel_id,
            "application/0",
            {"extensions": ",".join(extensions)},
        )
    assert harness.charm.legacy_db_relation._get_extensions(relation) == (
        [extensions[1], extensions[2]],
        {extensions[1].split(":")[0], extensions[2]},
    )

    # Test when one of the plugins/extensions is enabled.
    config = """options:
      plugin_citext_enable:
        default: true
        type: boolean
      plugin_debversion_enable:
        default: false
        type: boolean"""
    harness = Harness(PostgresqlOperatorCharm, config=config)
    harness.cleanup()
    harness.begin()
    assert harness.charm.legacy_db_relation._get_extensions(relation) == (
        [extensions[1], extensions[2]],
        {extensions[2]},
    )


def test_set_up_relation(harness):
    with (
        patch("charm.PostgresqlOperatorCharm.update_config"),
        patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock,
        patch("relations.db.DbProvides._update_unit_status") as _update_unit_status,
        patch("relations.db.new_password", return_value="test-password") as _new_password,
        patch("relations.db.DbProvides._get_extensions") as _get_extensions,
        patch("relations.db.logger") as _logger,
    ):
        rel_id = harness.model.get_relation(RELATION_NAME).id
        # Define some mocks' side effects.
        extensions = ["citext:public", "debversion"]
        _get_extensions.side_effect = [
            (extensions, {"debversion"}),
            (extensions, set()),
            (extensions, set()),
            (extensions, set()),
            (extensions, set()),
            (extensions, set()),
            (extensions, set()),
        ]
        postgresql_mock.create_user = PropertyMock(
            side_effect=[None, None, PostgreSQLCreateUserError, None, None]
        )
        postgresql_mock.create_database = PropertyMock(
            side_effect=[None, None, PostgreSQLCreateDatabaseError, None]
        )
        postgresql_mock.get_postgresql_version = PropertyMock(return_value=POSTGRESQL_VERSION)

        # Assert no operation is done when at least one of the requested extensions
        # is disabled.
        relation = harness.model.get_relation(RELATION_NAME, rel_id)
        assert not harness.charm.legacy_db_relation.set_up_relation(relation)
        postgresql_mock.create_user.assert_not_called()
        postgresql_mock.create_database.assert_not_called()
        postgresql_mock.get_postgresql_version.assert_not_called()
        _update_unit_status.assert_not_called()

        # Assert that the correct calls were made in a successful setup.
        harness.charm.unit.status = ActiveStatus()
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id,
                "application",
                {"database": DATABASE},
            )
        assert harness.charm.legacy_db_relation.set_up_relation(relation)
        user = f"relation_id_{rel_id}"
        postgresql_mock.create_user.assert_called_once_with(
            user, "test-password", False, extra_user_roles=[ACCESS_GROUP_RELATION]
        )
        postgresql_mock.create_database.assert_called_once_with(
            DATABASE, user, plugins=["pgaudit"], client_relations=[relation]
        )
        assert postgresql_mock.get_postgresql_version.call_count == 1
        _update_unit_status.assert_called_once()
        expected_data = {
            "allowed-units": "application/0",
            "database": DATABASE,
            "extensions": ",".join(extensions),
            "host": f"postgresql-k8s-0.postgresql-k8s-endpoints.{harness.model.name}.svc.cluster.local",
            "master": f"dbname={DATABASE} fallback_application_name=application "
            f"host=postgresql-k8s-primary.{harness.model.name}.svc.cluster.local "
            f"password=test-password port=5432 user=relation_id_{rel_id}",
            "password": "test-password",
            "port": DATABASE_PORT,
            "standbys": f"dbname={DATABASE} fallback_application_name=application "
            f"host=postgresql-k8s-replicas.{harness.model.name}.svc.cluster.local "
            f"password=test-password port=5432 user=relation_id_{rel_id}",
            "user": f"relation_id_{rel_id}",
            "version": POSTGRESQL_VERSION,
        }
        assert harness.get_relation_data(rel_id, harness.charm.app.name) == expected_data
        assert harness.get_relation_data(rel_id, harness.charm.unit.name) == expected_data
        assert not isinstance(harness.model.unit.status, BlockedStatus)

        # Assert that the correct calls were made when the database name is
        # provided only in the unit databag.
        postgresql_mock.create_user.reset_mock()
        postgresql_mock.create_database.reset_mock()
        postgresql_mock.get_postgresql_version.reset_mock()
        _update_unit_status.reset_mock()
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id,
                "application",
                {"database": ""},
            )
            harness.update_relation_data(
                rel_id,
                "application/0",
                {"database": DATABASE},
            )
            clear_relation_data(harness)
        assert harness.charm.legacy_db_relation.set_up_relation(relation)
        postgresql_mock.create_user.assert_called_once_with(
            user, "test-password", False, extra_user_roles=[ACCESS_GROUP_RELATION]
        )
        postgresql_mock.create_database.assert_called_once_with(
            DATABASE, user, plugins=["pgaudit"], client_relations=[relation]
        )
        assert postgresql_mock.get_postgresql_version.call_count == 1
        _update_unit_status.assert_called_once()
        assert harness.get_relation_data(rel_id, harness.charm.app.name) == expected_data
        assert harness.get_relation_data(rel_id, harness.charm.unit.name) == expected_data
        assert not isinstance(harness.model.unit.status, BlockedStatus)

        # Assert that the correct calls were made when the database name is not provided.
        postgresql_mock.create_user.reset_mock()
        postgresql_mock.create_database.reset_mock()
        postgresql_mock.get_postgresql_version.reset_mock()
        _update_unit_status.reset_mock()
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id,
                "application/0",
                {"database": ""},
            )
            clear_relation_data(harness)
        assert not harness.charm.legacy_db_relation.set_up_relation(relation)
        postgresql_mock.create_user.assert_not_called()
        postgresql_mock.create_database.assert_not_called()
        postgresql_mock.get_postgresql_version.assert_not_called()
        _update_unit_status.assert_not_called()
        # No data is set in the databags by the database.
        assert harness.get_relation_data(rel_id, harness.charm.app.name) == {}
        assert harness.get_relation_data(rel_id, harness.charm.unit.name) == {}
        assert not isinstance(harness.model.unit.status, BlockedStatus)

        # BlockedStatus due to a PostgreSQLCreateUserError.
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id,
                "application",
                {"database": DATABASE},
            )
        assert not harness.charm.legacy_db_relation.set_up_relation(relation)
        postgresql_mock.create_database.assert_not_called()
        postgresql_mock.get_postgresql_version.assert_not_called()
        _update_unit_status.assert_not_called()
        assert isinstance(harness.model.unit.status, BlockedStatus)
        # No data is set in the databags by the database.
        assert harness.get_relation_data(rel_id, harness.charm.app.name) == {}
        assert harness.get_relation_data(rel_id, harness.charm.unit.name) == {}

        # BlockedStatus due to a PostgreSQLCreateDatabaseError.
        harness.charm.unit.status = ActiveStatus()
        assert not harness.charm.legacy_db_relation.set_up_relation(relation)
        postgresql_mock.get_postgresql_version.assert_not_called()
        _update_unit_status.assert_not_called()
        assert isinstance(harness.model.unit.status, BlockedStatus)
        # No data is set in the databags by the database.
        assert harness.get_relation_data(rel_id, harness.charm.app.name) == {}
        assert harness.get_relation_data(rel_id, harness.charm.unit.name) == {}

        # version is not updated due to a PostgreSQLGetPostgreSQLVersionError.
        postgresql_mock.get_postgresql_version.side_effect = PostgreSQLGetPostgreSQLVersionError
        harness.charm.unit.status = ActiveStatus()
        assert harness.charm.legacy_db_relation.set_up_relation(relation)
        _logger.exception.assert_called_once_with(
            "Failed to retrieve the PostgreSQL version to initialise/update db relation"
        )


def test_update_unit_status(harness):
    with (
        patch(
            "relations.db.DbProvides._check_for_blocking_relations"
        ) as _check_for_blocking_relations,
        patch(
            "charm.PostgresqlOperatorCharm._has_blocked_status", new_callable=PropertyMock
        ) as _has_blocked_status,
    ):
        rel_id = harness.model.get_relation(RELATION_NAME).id
        # Test when the charm is not blocked.
        relation = harness.model.get_relation(RELATION_NAME, rel_id)
        _has_blocked_status.return_value = False
        harness.charm.legacy_db_relation._update_unit_status(relation)
        _check_for_blocking_relations.assert_not_called()
        assert not isinstance(harness.charm.unit.status, ActiveStatus)

        # Test when the charm is blocked but not due to extensions request.
        _has_blocked_status.return_value = True
        harness.charm.unit.status = BlockedStatus("fake message")
        harness.charm.legacy_db_relation._update_unit_status(relation)
        _check_for_blocking_relations.assert_not_called()
        assert not isinstance(harness.charm.unit.status, ActiveStatus)

        # Test when there are relations causing the blocked status.
        harness.charm.unit.status = BlockedStatus("extensions requested through relation")
        _check_for_blocking_relations.return_value = True
        harness.charm.legacy_db_relation._update_unit_status(relation)
        _check_for_blocking_relations.assert_called_once_with(relation.id)
        assert not isinstance(harness.charm.unit.status, ActiveStatus)

        # Test when there are no relations causing the blocked status anymore.
        _check_for_blocking_relations.reset_mock()
        _check_for_blocking_relations.return_value = False
        harness.charm.legacy_db_relation._update_unit_status(relation)
        _check_for_blocking_relations.assert_called_once_with(relation.id)
        assert isinstance(harness.charm.unit.status, ActiveStatus)


def test_on_relation_departed(harness):
    with patch("charm.Patroni.member_started", new_callable=PropertyMock(return_value=True)):
        # Test when this unit is departing the relation (due to a scale down event).
        peer_rel_id = harness.model.get_relation(PEER).id
        assert "departing" not in harness.get_relation_data(peer_rel_id, harness.charm.unit)
        event = Mock()
        event.relation.data = {harness.charm.app: {}, harness.charm.unit: {}}
        event.departing_unit = harness.charm.unit
        harness.charm.legacy_db_relation._on_relation_departed(event)
        assert "departing" in harness.get_relation_data(peer_rel_id, harness.charm.unit)

        # Test when this unit is departing the relation (due to the relation being broken between the apps).
        with harness.hooks_disabled():
            harness.update_relation_data(peer_rel_id, harness.charm.unit.name, {"departing": ""})
        event.relation.data = {harness.charm.app: {}, harness.charm.unit: {}}
        event.departing_unit = Unit(f"{harness.charm.app}/1", None, harness.charm.app._backend, {})
        harness.charm.legacy_db_relation._on_relation_departed(event)
        relation_data = harness.get_relation_data(peer_rel_id, harness.charm.unit)
        assert "departing" not in relation_data


def test_on_relation_broken(harness):
    with (
        patch("charm.PostgresqlOperatorCharm.update_config"),
        patch(
            "charm.Patroni.member_started", new_callable=PropertyMock(return_value=True)
        ) as _member_started,
    ):
        rel_id = harness.model.get_relation(RELATION_NAME).id
        peer_rel_id = harness.model.get_relation(PEER).id
        with harness.hooks_disabled():
            harness.set_leader()
        with patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock:
            # Test when this unit is departing the relation (due to the relation being broken between the apps).
            event = Mock()
            event.relation.id = rel_id
            harness.charm.legacy_db_relation._on_relation_broken(event)
            user = f"relation_id_{rel_id}"
            postgresql_mock.delete_user.assert_called_once_with(user)

            # Test when this unit is departing the relation (due to a scale down event).
            postgresql_mock.reset_mock()
            with harness.hooks_disabled():
                harness.update_relation_data(
                    peer_rel_id, harness.charm.unit.name, {"departing": "True"}
                )
            harness.charm.legacy_db_relation._on_relation_broken(event)
            postgresql_mock.delete_user.assert_not_called()


def test_on_relation_broken_extensions_unblock(harness):
    with (
        patch("charm.PostgresqlOperatorCharm.update_config"),
        patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock,
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint",
            new_callable=PropertyMock,
        ) as _primary_endpoint,
        patch(
            "charm.PostgresqlOperatorCharm._has_blocked_status", new_callable=PropertyMock
        ) as _has_blocked_status,
        patch("charm.Patroni.member_started", new_callable=PropertyMock) as _member_started,
        patch("charm.DbProvides._on_relation_departed") as _on_relation_departed,
    ):
        rel_id = harness.model.get_relation(RELATION_NAME).id
        # Set some side effects to test multiple situations.
        _has_blocked_status.return_value = True
        _member_started.return_value = True
        _primary_endpoint.return_value = {"1.1.1.1"}
        postgresql_mock.delete_user = PropertyMock(return_value=None)
        harness.model.unit.status = BlockedStatus("extensions requested through relation")
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id,
                "application",
                {"database": DATABASE, "extensions": "test"},
            )

        # Break the relation that blocked the charm.
        harness.remove_relation(rel_id)
        assert isinstance(harness.model.unit.status, ActiveStatus)


def test_on_relation_broken_extensions_keep_block(harness):
    with (
        patch("charm.PostgresqlOperatorCharm.update_config"),
        patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock,
        patch(
            "charm.PostgresqlOperatorCharm.primary_endpoint",
            new_callable=PropertyMock,
        ) as _primary_endpoint,
        patch("charm.PostgresqlOperatorCharm.is_blocked", new_callable=PropertyMock) as is_blocked,
        patch("charm.Patroni.member_started", new_callable=PropertyMock) as _member_started,
        patch("charm.DbProvides._on_relation_departed") as _on_relation_departed,
    ):
        # Set some side effects to test multiple situations.
        is_blocked.return_value = True
        _member_started.return_value = True
        _primary_endpoint.return_value = {"1.1.1.1"}
        postgresql_mock.delete_user = PropertyMock(return_value=None)
        harness.model.unit.status = BlockedStatus(
            "extensions requested through relation, enable them through config options"
        )
        with harness.hooks_disabled():
            first_rel_id = harness.add_relation(RELATION_NAME, "application1")
            harness.update_relation_data(
                first_rel_id,
                "application1",
                {"database": DATABASE, "extensions": "test"},
            )
            second_rel_id = harness.add_relation(RELATION_NAME, "application2")
            harness.update_relation_data(
                second_rel_id,
                "application2",
                {"database": DATABASE, "extensions": "test"},
            )

        event = Mock()
        event.relation.id = first_rel_id
        # Break one of the relations that block the charm.
        harness.charm.legacy_db_relation._on_relation_broken(event)
        assert isinstance(harness.model.unit.status, BlockedStatus)
