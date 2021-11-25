# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import re
import unittest
from unittest.mock import Mock, patch

from ops.model import ActiveStatus, WaitingStatus
from ops.testing import Harness

from charm import PostgresqlOperatorCharm


class TestCharm(unittest.TestCase):
    def setUp(self):
        self._peer_relation = "postgresql-replicas"
        self._postgresql_container = "postgresql"
        self._postgresql_service = "postgresql"

        self.harness = Harness(PostgresqlOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()
        self.charm = self.harness.charm

    def test_on_leader_elected(self):
        # Assert that there is no password in the peer relation.
        self.harness.add_relation(self._peer_relation, self.charm.app.name)
        self.assertIsNone(self.charm._peers.data[self.charm.app].get("postgres-password", None))

        # Check that a new password was generated on leader election.
        self.harness.set_leader()
        password = self.charm._peers.data[self.charm.app].get("postgres-password", None)
        self.assertIsNotNone(password)

        # Trigger a new leader election and check that the password is still the same.
        self.harness.set_leader(False)
        self.harness.set_leader()
        self.assertEqual(
            self.charm._peers.data[self.charm.app].get("postgres-password", None), password
        )

    def test_on_postgresql_pebble_ready(self):
        # Check that the initial plan is empty.
        plan = self.harness.get_container_pebble_plan(self._postgresql_container)
        self.assertEqual(plan.to_dict(), {})

        # Trigger a pebble-ready hook and test the status before we can connect to the container.
        self.harness.add_relation(self._peer_relation, self.charm.app.name)
        with patch("ops.model.Container.can_connect") as _can_connect:
            _can_connect.return_value = False
            self.harness.container_pebble_ready(self._postgresql_container)
            self.assertEqual(
                self.harness.model.unit.status,
                WaitingStatus("waiting for Pebble in workload container"),
            )

        # Get the current and the expected layer from the pebble plan and the _postgresql_layer
        # method, respectively.
        self.harness.container_pebble_ready(self._postgresql_container)
        plan = self.harness.get_container_pebble_plan(self._postgresql_container)
        expected = self.charm._postgresql_layer().to_dict()
        expected.pop("summary", "")
        expected.pop("description", "")
        # Check the plan is as expected.
        self.assertEqual(plan.to_dict(), expected)
        self.assertEqual(self.harness.model.unit.status, ActiveStatus())
        container = self.harness.model.unit.get_container(self._postgresql_container)
        self.assertEqual(container.get_service(self._postgresql_service).is_running(), True)

    @patch("charm.PostgresqlOperatorCharm._get_postgres_password")
    def test_on_get_postgres_password(self, _get_postgres_password):
        mock_event = Mock()
        _get_postgres_password.return_value = "test-password"
        self.charm._on_get_postgres_password(mock_event)
        _get_postgres_password.assert_called_once()
        mock_event.set_results.assert_called_once_with({"postgres-password": "test-password"})

    def test_postgresql_layer(self):
        # Test with the already generated password.
        self.harness.add_relation(self._peer_relation, self.charm.app.name)
        self.harness.set_leader()
        plan = self.charm._postgresql_layer().to_dict()
        expected = {
            "summary": "postgresql layer",
            "description": "pebble config layer for postgresql",
            "services": {
                self._postgresql_service: {
                    "override": "replace",
                    "summary": "entrypoint of the postgresql image",
                    "command": "/usr/local/bin/docker-entrypoint.sh postgres",
                    "startup": "enabled",
                    "environment": {
                        "PGDATA": "/var/lib/postgresql/data/pgdata",
                        "POSTGRES_PASSWORD": self.charm._get_postgres_password(),
                    },
                }
            },
        }
        self.assertEqual(plan, expected)

    def test_new_password(self):
        # Test the password generation twice in order to check if we get different passwords and
        # that they meet the required criteria.
        first_password = self.charm._new_password()
        self.assertEqual(len(first_password), 16)
        self.assertIsNotNone(re.fullmatch("[a-zA-Z0-9\b]{16}$", first_password))

        second_password = self.charm._new_password()
        self.assertIsNotNone(re.fullmatch("[a-zA-Z0-9\b]{16}$", second_password))
        self.assertNotEqual(second_password, first_password)

    def test_get_postgres_password(self):
        # Test for a None password.
        self.harness.add_relation(self._peer_relation, self.charm.app.name)
        self.assertIsNone(self.charm._get_postgres_password())

        # Then test for a non empty password after leader election and peer data set.
        self.harness.set_leader()
        password = self.charm._get_postgres_password()
        self.assertIsNotNone(password)
        self.assertNotEqual(password, "")
