# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import unittest
from unittest.mock import Mock, PropertyMock, patch

from charms.postgresql_k8s.v0.postgresql import (
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


class TestDbProvides(unittest.TestCase):
    @patch("charm.KubernetesServicePatch", lambda x, y: None)
    def setUp(self):
        self.harness = Harness(PostgresqlOperatorCharm)
        self.addCleanup(self.harness.cleanup)

        # Set up the initial relation and hooks.
        self.harness.set_leader(True)
        self.harness.begin()
        self.app = self.harness.charm.app.name
        self.unit = self.harness.charm.unit.name

        # Define some relations.
        self.rel_id = self.harness.add_relation(RELATION_NAME, "application")
        self.harness.add_relation_unit(self.rel_id, "application/0")
        self.peer_rel_id = self.harness.add_relation(PEER, self.app)
        self.harness.add_relation_unit(self.peer_rel_id, f"{self.app}/1")
        self.harness.add_relation_unit(self.peer_rel_id, self.unit)
        self.harness.update_relation_data(
            self.peer_rel_id,
            self.app,
            {"cluster_initialised": "True"},
        )
        self.legacy_db_relation = self.harness.charm.legacy_db_relation

    def clear_relation_data(self):
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
        self.harness.update_relation_data(self.rel_id, self.app, data)
        self.harness.update_relation_data(self.rel_id, self.unit, data)

    def request_database(self):
        # Reset the charm status.
        self.harness.model.unit.status = ActiveStatus()

        with self.harness.hooks_disabled():
            # Reset the application databag.
            self.harness.update_relation_data(
                self.rel_id,
                "application/0",
                {"database": ""},
            )

            # Reset the database databag.
            self.clear_relation_data()

        # Simulate the request of a new database.
        self.harness.update_relation_data(
            self.rel_id,
            "application/0",
            {"database": DATABASE},
        )

    @patch("charm.DbProvides.set_up_relation")
    @patch.object(EventBase, "defer")
    @patch("charm.Patroni.member_started", new_callable=PropertyMock)
    def test_on_relation_changed(
        self,
        _member_started,
        _defer,
        _set_up_relation,
    ):
        # Set some side effects to test multiple situations.
        _member_started.side_effect = [False, False, True, True]

        # Request a database before the cluster is initialised.
        self.request_database()
        _defer.assert_called_once()
        _set_up_relation.assert_not_called()

        # Request a database before the database is ready.
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.peer_rel_id,
                self.harness.charm.app.name,
                {"cluster_initialised": "True"},
            )
        self.request_database()
        self.assertEqual(_defer.call_count, 2)
        _set_up_relation.assert_not_called()

        # Request a database to a non leader unit.
        _defer.reset_mock()
        with self.harness.hooks_disabled():
            self.harness.set_leader(False)
        self.request_database()
        _defer.assert_not_called()
        _set_up_relation.assert_not_called()

        # Request it again in a leader unit.
        with self.harness.hooks_disabled():
            self.harness.set_leader()
        self.request_database()
        _defer.assert_not_called()
        _set_up_relation.assert_called_once()

    @patch("charm.KubernetesServicePatch", lambda x, y: None)
    def test_get_extensions(self):
        # Test when there are no extensions in the relation databags.
        relation = self.harness.model.get_relation(RELATION_NAME, self.rel_id)
        self.assertEqual(
            self.harness.charm.legacy_db_relation._get_extensions(relation), ([], set())
        )

        # Test when there are extensions in the application relation databag.
        extensions = ["", "citext:public", "debversion"]
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.rel_id,
                "application",
                {"extensions": ",".join(extensions)},
            )
        self.assertEqual(
            self.harness.charm.legacy_db_relation._get_extensions(relation),
            ([extensions[1], extensions[2]], {extensions[1].split(":")[0], extensions[2]}),
        )

        # Test when there are extensions in the unit relation databag.
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.rel_id,
                "application",
                {"extensions": ""},
            )
            self.harness.update_relation_data(
                self.rel_id,
                "application/0",
                {"extensions": ",".join(extensions)},
            )
        self.assertEqual(
            self.harness.charm.legacy_db_relation._get_extensions(relation),
            ([extensions[1], extensions[2]], {extensions[1].split(":")[0], extensions[2]}),
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
        self.addCleanup(harness.cleanup)
        harness.begin()
        self.assertEqual(
            harness.charm.legacy_db_relation._get_extensions(relation),
            ([extensions[1], extensions[2]], {extensions[2]}),
        )

    @patch("relations.db.DbProvides._update_unit_status")
    @patch("relations.db.new_password", return_value="test-password")
    @patch("relations.db.DbProvides._get_extensions")
    def test_set_up_relation(
        self,
        _get_extensions,
        _new_password,
        _update_unit_status,
    ):
        with patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock:
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
            postgresql_mock.get_postgresql_version = PropertyMock(
                side_effect=[
                    POSTGRESQL_VERSION,
                    POSTGRESQL_VERSION,
                    POSTGRESQL_VERSION,
                    POSTGRESQL_VERSION,
                    POSTGRESQL_VERSION,
                    PostgreSQLGetPostgreSQLVersionError,
                ]
            )

            # Assert no operation is done when at least one of the requested extensions
            # is disabled.
            relation = self.harness.model.get_relation(RELATION_NAME, self.rel_id)
            self.assertFalse(self.harness.charm.legacy_db_relation.set_up_relation(relation))
            postgresql_mock.create_user.assert_not_called()
            postgresql_mock.create_database.assert_not_called()
            postgresql_mock.get_postgresql_version.assert_not_called()
            _update_unit_status.assert_not_called()

            # Assert that the correct calls were made in a successful setup.
            self.harness.charm.unit.status = ActiveStatus()
            with self.harness.hooks_disabled():
                self.harness.update_relation_data(
                    self.rel_id,
                    "application",
                    {"database": DATABASE},
                )
            self.assertTrue(self.harness.charm.legacy_db_relation.set_up_relation(relation))
            user = f"relation_id_{self.rel_id}"
            postgresql_mock.create_user.assert_called_once_with(user, "test-password", False)
            postgresql_mock.create_database.assert_called_once_with(
                DATABASE, user, plugins=[], client_relations=[relation]
            )
            self.assertEqual(postgresql_mock.get_postgresql_version.call_count, 2)
            _update_unit_status.assert_called_once()
            expected_data = {
                "allowed-units": "application/0",
                "database": DATABASE,
                "extensions": ",".join(extensions),
                "host": f"postgresql-k8s-0.postgresql-k8s-endpoints.{self.harness.model.name}.svc.cluster.local",
                "master": f"dbname={DATABASE} fallback_application_name=application "
                f"host=postgresql-k8s-primary.{self.harness.model.name}.svc.cluster.local "
                f"password=test-password port=5432 user=relation_id_{self.rel_id}",
                "password": "test-password",
                "port": DATABASE_PORT,
                "standbys": f"dbname={DATABASE} fallback_application_name=application "
                f"host=postgresql-k8s-replicas.{self.harness.model.name}.svc.cluster.local "
                f"password=test-password port=5432 user=relation_id_{self.rel_id}",
                "user": f"relation_id_{self.rel_id}",
                "version": POSTGRESQL_VERSION,
            }
            self.assertEqual(self.harness.get_relation_data(self.rel_id, self.app), expected_data)
            self.assertEqual(self.harness.get_relation_data(self.rel_id, self.unit), expected_data)
            self.assertNotIsInstance(self.harness.model.unit.status, BlockedStatus)

            # Assert that the correct calls were made when the database name is
            # provided only in the unit databag.
            postgresql_mock.create_user.reset_mock()
            postgresql_mock.create_database.reset_mock()
            postgresql_mock.get_postgresql_version.reset_mock()
            _update_unit_status.reset_mock()
            with self.harness.hooks_disabled():
                self.harness.update_relation_data(
                    self.rel_id,
                    "application",
                    {"database": ""},
                )
                self.harness.update_relation_data(
                    self.rel_id,
                    "application/0",
                    {"database": DATABASE},
                )
                self.clear_relation_data()
            self.assertTrue(self.harness.charm.legacy_db_relation.set_up_relation(relation))
            postgresql_mock.create_user.assert_called_once_with(user, "test-password", False)
            postgresql_mock.create_database.assert_called_once_with(
                DATABASE, user, plugins=[], client_relations=[relation]
            )
            self.assertEqual(postgresql_mock.get_postgresql_version.call_count, 2)
            _update_unit_status.assert_called_once()
            self.assertEqual(self.harness.get_relation_data(self.rel_id, self.app), expected_data)
            self.assertEqual(self.harness.get_relation_data(self.rel_id, self.unit), expected_data)
            self.assertNotIsInstance(self.harness.model.unit.status, BlockedStatus)

            # Assert that the correct calls were made when the database name is not provided.
            postgresql_mock.create_user.reset_mock()
            postgresql_mock.create_database.reset_mock()
            postgresql_mock.get_postgresql_version.reset_mock()
            _update_unit_status.reset_mock()
            with self.harness.hooks_disabled():
                self.harness.update_relation_data(
                    self.rel_id,
                    "application/0",
                    {"database": ""},
                )
                self.clear_relation_data()
            self.assertFalse(self.harness.charm.legacy_db_relation.set_up_relation(relation))
            postgresql_mock.create_user.assert_not_called()
            postgresql_mock.create_database.assert_not_called()
            postgresql_mock.get_postgresql_version.assert_not_called()
            _update_unit_status.assert_not_called()
            # No data is set in the databags by the database.
            self.assertEqual(self.harness.get_relation_data(self.rel_id, self.app), {})
            self.assertEqual(self.harness.get_relation_data(self.rel_id, self.unit), {})
            self.assertNotIsInstance(self.harness.model.unit.status, BlockedStatus)

            # BlockedStatus due to a PostgreSQLCreateUserError.
            with self.harness.hooks_disabled():
                self.harness.update_relation_data(
                    self.rel_id,
                    "application",
                    {"database": DATABASE},
                )
            self.assertFalse(self.harness.charm.legacy_db_relation.set_up_relation(relation))
            postgresql_mock.create_database.assert_not_called()
            postgresql_mock.get_postgresql_version.assert_not_called()
            _update_unit_status.assert_not_called()
            self.assertIsInstance(self.harness.model.unit.status, BlockedStatus)
            # No data is set in the databags by the database.
            self.assertEqual(self.harness.get_relation_data(self.rel_id, self.app), {})
            self.assertEqual(self.harness.get_relation_data(self.rel_id, self.unit), {})

            # BlockedStatus due to a PostgreSQLCreateDatabaseError.
            self.harness.charm.unit.status = ActiveStatus()
            self.assertFalse(self.harness.charm.legacy_db_relation.set_up_relation(relation))
            postgresql_mock.get_postgresql_version.assert_not_called()
            _update_unit_status.assert_not_called()
            self.assertIsInstance(self.harness.model.unit.status, BlockedStatus)
            # No data is set in the databags by the database.
            self.assertEqual(self.harness.get_relation_data(self.rel_id, self.app), {})
            self.assertEqual(self.harness.get_relation_data(self.rel_id, self.unit), {})

            # BlockedStatus due to a PostgreSQLGetPostgreSQLVersionError.
            self.harness.charm.unit.status = ActiveStatus()
            self.assertFalse(self.harness.charm.legacy_db_relation.set_up_relation(relation))
            _update_unit_status.assert_not_called()
            self.assertIsInstance(self.harness.model.unit.status, BlockedStatus)

    @patch("relations.db.DbProvides._check_for_blocking_relations")
    @patch("charm.PostgresqlOperatorCharm._has_blocked_status", new_callable=PropertyMock)
    def test_update_unit_status(self, _has_blocked_status, _check_for_blocking_relations):
        # Test when the charm is not blocked.
        relation = self.harness.model.get_relation(RELATION_NAME, self.rel_id)
        _has_blocked_status.return_value = False
        self.harness.charm.legacy_db_relation._update_unit_status(relation)
        _check_for_blocking_relations.assert_not_called()
        self.assertNotIsInstance(self.harness.charm.unit.status, ActiveStatus)

        # Test when the charm is blocked but not due to extensions request.
        _has_blocked_status.return_value = True
        self.harness.charm.unit.status = BlockedStatus("fake message")
        self.harness.charm.legacy_db_relation._update_unit_status(relation)
        _check_for_blocking_relations.assert_not_called()
        self.assertNotIsInstance(self.harness.charm.unit.status, ActiveStatus)

        # Test when there are relations causing the blocked status.
        self.harness.charm.unit.status = BlockedStatus("extensions requested through relation")
        _check_for_blocking_relations.return_value = True
        self.harness.charm.legacy_db_relation._update_unit_status(relation)
        _check_for_blocking_relations.assert_called_once_with(relation.id)
        self.assertNotIsInstance(self.harness.charm.unit.status, ActiveStatus)

        # Test when there are no relations causing the blocked status anymore.
        _check_for_blocking_relations.reset_mock()
        _check_for_blocking_relations.return_value = False
        self.harness.charm.legacy_db_relation._update_unit_status(relation)
        _check_for_blocking_relations.assert_called_once_with(relation.id)
        self.assertIsInstance(self.harness.charm.unit.status, ActiveStatus)

    @patch("charm.Patroni.member_started", new_callable=PropertyMock(return_value=True))
    def test_on_relation_departed(self, _):
        # Test when this unit is departing the relation (due to a scale down event).
        self.assertNotIn(
            "departing", self.harness.get_relation_data(self.peer_rel_id, self.harness.charm.unit)
        )
        event = Mock()
        event.relation.data = {self.harness.charm.app: {}, self.harness.charm.unit: {}}
        event.departing_unit = self.harness.charm.unit
        self.harness.charm.legacy_db_relation._on_relation_departed(event)
        self.assertIn(
            "departing", self.harness.get_relation_data(self.peer_rel_id, self.harness.charm.unit)
        )

        # Test when this unit is departing the relation (due to the relation being broken between the apps).
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.peer_rel_id, self.harness.charm.unit.name, {"departing": ""}
            )
        event.relation.data = {self.harness.charm.app: {}, self.harness.charm.unit: {}}
        event.departing_unit = Unit(
            f"{self.harness.charm.app}/1", None, self.harness.charm.app._backend, {}
        )
        self.harness.charm.legacy_db_relation._on_relation_departed(event)
        relation_data = self.harness.get_relation_data(self.peer_rel_id, self.harness.charm.unit)
        self.assertNotIn("departing", relation_data)

    @patch("charm.Patroni.member_started", new_callable=PropertyMock(return_value=True))
    def test_on_relation_broken(self, _member_started):
        with self.harness.hooks_disabled():
            self.harness.set_leader()
        with patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock:
            # Test when this unit is departing the relation (due to the relation being broken between the apps).
            event = Mock()
            event.relation.id = self.rel_id
            self.harness.charm.legacy_db_relation._on_relation_broken(event)
            user = f"relation_id_{self.rel_id}"
            postgresql_mock.delete_user.assert_called_once_with(user)

            # Test when this unit is departing the relation (due to a scale down event).
            postgresql_mock.reset_mock()
            with self.harness.hooks_disabled():
                self.harness.update_relation_data(
                    self.peer_rel_id, self.harness.charm.unit.name, {"departing": "True"}
                )
            self.harness.charm.legacy_db_relation._on_relation_broken(event)
            postgresql_mock.delete_user.assert_not_called()

    @patch(
        "charm.PostgresqlOperatorCharm.primary_endpoint",
        new_callable=PropertyMock,
    )
    @patch("charm.PostgresqlOperatorCharm._has_blocked_status", new_callable=PropertyMock)
    @patch("charm.Patroni.member_started", new_callable=PropertyMock)
    @patch("charm.DbProvides._on_relation_departed")
    def test_on_relation_broken_extensions_unblock(
        self, _on_relation_departed, _member_started, _primary_endpoint, _has_blocked_status
    ):
        with patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock:
            # Set some side effects to test multiple situations.
            _has_blocked_status.return_value = True
            _member_started.return_value = True
            _primary_endpoint.return_value = {"1.1.1.1"}
            postgresql_mock.delete_user = PropertyMock(return_value=None)
            self.harness.model.unit.status = BlockedStatus("extensions requested through relation")
            with self.harness.hooks_disabled():
                self.harness.update_relation_data(
                    self.rel_id,
                    "application",
                    {"database": DATABASE, "extensions": "test"},
                )

            # Break the relation that blocked the charm.
            self.harness.remove_relation(self.rel_id)
            self.assertTrue(isinstance(self.harness.model.unit.status, ActiveStatus))

    @patch(
        "charm.PostgresqlOperatorCharm.primary_endpoint",
        new_callable=PropertyMock,
    )
    @patch("charm.PostgresqlOperatorCharm.is_blocked", new_callable=PropertyMock)
    @patch("charm.Patroni.member_started", new_callable=PropertyMock)
    @patch("charm.DbProvides._on_relation_departed")
    def test_on_relation_broken_extensions_keep_block(
        self, _on_relation_departed, _member_started, _primary_endpoint, is_blocked
    ):
        with patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock:
            # Set some side effects to test multiple situations.
            is_blocked.return_value = True
            _member_started.return_value = True
            _primary_endpoint.return_value = {"1.1.1.1"}
            postgresql_mock.delete_user = PropertyMock(return_value=None)
            self.harness.model.unit.status = BlockedStatus(
                "extensions requested through relation, enable them through config options"
            )
            with self.harness.hooks_disabled():
                first_rel_id = self.harness.add_relation(RELATION_NAME, "application1")
                self.harness.update_relation_data(
                    first_rel_id,
                    "application1",
                    {"database": DATABASE, "extensions": "test"},
                )
                second_rel_id = self.harness.add_relation(RELATION_NAME, "application2")
                self.harness.update_relation_data(
                    second_rel_id,
                    "application2",
                    {"database": DATABASE, "extensions": "test"},
                )

            event = Mock()
            event.relation.id = first_rel_id
            # Break one of the relations that block the charm.
            self.harness.charm.legacy_db_relation._on_relation_broken(event)
            self.assertTrue(isinstance(self.harness.model.unit.status, BlockedStatus))
