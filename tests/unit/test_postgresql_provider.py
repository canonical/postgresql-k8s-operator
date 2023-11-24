# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import unittest
from unittest.mock import Mock, PropertyMock, patch

from ops import Unit
from ops.testing import Harness

from charm import PostgresqlOperatorCharm
from constants import PEER
from tests.helpers import patch_network_get

DATABASE = "test_database"
EXTRA_USER_ROLES = "CREATEDB,CREATEROLE"
RELATION_NAME = "database"
POSTGRESQL_VERSION = "14"


@patch_network_get(private_address="1.1.1.1")
class TestPostgreSQLProvider(unittest.TestCase):
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
        self.harness.add_relation_unit(self.peer_rel_id, self.unit)
        self.harness.update_relation_data(
            self.peer_rel_id,
            self.app,
            {"cluster_initialised": "True"},
        )
        self.provider = self.harness.charm.postgresql_client_relation

    @patch("charm.Patroni.member_started", new_callable=PropertyMock(return_value=True))
    def test_on_relation_departed(self, _):
        # Test when this unit is departing the relation (due to a scale down event).
        self.assertNotIn(
            "departing", self.harness.get_relation_data(self.peer_rel_id, self.harness.charm.unit)
        )
        event = Mock()
        event.relation.data = {self.harness.charm.app: {}, self.harness.charm.unit: {}}
        event.departing_unit = self.harness.charm.unit
        self.harness.charm.postgresql_client_relation._on_relation_departed(event)
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
        self.harness.charm.postgresql_client_relation._on_relation_departed(event)
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
            self.harness.charm.postgresql_client_relation._on_relation_broken(event)
            user = f"relation_id_{self.rel_id}"
            postgresql_mock.delete_user.assert_called_once_with(user)

            # Test when this unit is departing the relation (due to a scale down event).
            postgresql_mock.reset_mock()
            with self.harness.hooks_disabled():
                self.harness.update_relation_data(
                    self.peer_rel_id, self.harness.charm.unit.name, {"departing": "True"}
                )
            self.harness.charm.postgresql_client_relation._on_relation_broken(event)
            postgresql_mock.delete_user.assert_not_called()
