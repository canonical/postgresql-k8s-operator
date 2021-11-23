# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import re
import unittest
from unittest.mock import patch

from ops.model import ActiveStatus, WaitingStatus
from ops.testing import Harness

from charm import PostgresqlOperatorCharm


class TestCharm(unittest.TestCase):
    def setUp(self):
        self._postgresql_service = "postgresql"

        self.harness = Harness(PostgresqlOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()
        self.charm = self.harness.charm

    def test_on_install(self):
        # Test the attribution of the postgres user password in charm install.
        with self.assertRaises(AttributeError):
            self.charm._stored.postgres_password
        self.charm.on.install.emit()
        self.assertIsNotNone(self.charm._stored.postgres_password)
        self.assertNotEqual(self.charm._stored.postgres_password, "")

    def test_on_postgresql_pebble_ready(self):
        # Check that the initial plan is empty.
        plan = self.harness.get_container_pebble_plan(self._postgresql_service)
        self.assertEqual(plan.to_dict(), {})

        # Trigger a pebble-ready hook and test the status before we can connect to the container.
        self.charm.on.install.emit()
        with patch("ops.model.Container.can_connect") as _can_connect:
            _can_connect.return_value = False
            self.harness.container_pebble_ready(self._postgresql_service)
            self.assertEqual(
                self.harness.model.unit.status,
                WaitingStatus("waiting for Pebble in workload container"),
            )

        # Get the current and the expected layer from the pebble plan and the _postgresql_layer
        # method, respectively.
        self.harness.container_pebble_ready(self._postgresql_service)
        plan = self.harness.get_container_pebble_plan(self._postgresql_service)
        expected = self.harness.charm._postgresql_layer().to_dict()
        expected.pop("summary", "")
        expected.pop("description", "")
        # Check the plan is as expected.
        self.assertEqual(plan.to_dict(), expected)
        self.assertEqual(self.harness.model.unit.status, ActiveStatus())
        container = self.harness.model.unit.get_container(self._postgresql_service)
        self.assertEqual(container.get_service(self._postgresql_service).is_running(), True)

    def test_postgresql_layer(self):
        # Test without generating postgres user password.
        with self.assertRaises(AttributeError):
            self.harness.charm._postgresql_layer()
        # And now test with the already generated password.
        self.charm.on.install.emit()
        plan = self.harness.charm._postgresql_layer().to_dict()
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
                        "POSTGRES_PASSWORD": self.charm._stored.postgres_password,
                    },
                }
            },
        }
        self.assertEqual(plan, expected)

    def test_new_password(self):
        # Test the password generation twice in order to check if we get different passwords and
        # that they meet the required criteria.
        first_password = self.harness.charm._new_password()
        self.assertEqual(len(first_password), 16)
        self.assertIsNotNone(re.fullmatch("[a-zA-Z0-9\b]{16}$", first_password))

        second_password = self.harness.charm._new_password()
        self.assertIsNotNone(re.fullmatch("[a-zA-Z0-9\b]{16}$", second_password))
        self.assertNotEqual(second_password, first_password)
