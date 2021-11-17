# Copyright 2021 Canonical
# See LICENSE file for licensing details.

import unittest

from charm import PostgresqlOperatorCharm
from ops.model import ActiveStatus
from ops.testing import Harness


class TestCharm(unittest.TestCase):
    def setUp(self):
        self.harness = Harness(PostgresqlOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def test_postgresql_layer(self):
        # Test with empty config.
        self.assertEqual(self.harness.charm.config["postgres-password"], "")
        expected = {
            "summary": "postgresql layer",
            "description": "pebble config layer for postgresql",
            "services": {
                "postgresql": {
                    "override": "replace",
                    "summary": "entrypoint of the postgresql image",
                    "command": "/usr/local/bin/docker-entrypoint.sh postgres",
                    "startup": "enabled",
                    "environment": {
                        "POSTGRES_PASSWORD": ""
                    }
                }
            },
        }
        self.assertEqual(self.harness.charm._postgresql_layer().to_dict(), expected)
        # And now test with a different value in the postgres-password config option.
        # Disable hook firing first.
        self.harness.disable_hooks()
        self.harness.update_config({"postgres-password": "SECRET_PASSWORD"})
        expected["services"]["postgresql"]["environment"]["POSTGRES_PASSWORD"] = "SECRET_PASSWORD"
        self.assertEqual(self.harness.charm._postgresql_layer().to_dict(), expected)

    def test_on_config_changed(self):
        plan = self.harness.get_container_pebble_plan("postgresql")
        self.assertEqual(plan.to_dict(), {})
        # Trigger a config-changed hook. Since there was no plan initially, the
        # "postgresql" service in the container won't be running so we'll be
        # testing the `is_running() == False` codepath.
        self.harness.update_config({"postgres-password": ""})
        plan = self.harness.get_container_pebble_plan("postgresql")
        # Get the expected layer from the postgresql_layer method (tested above)
        expected = self.harness.charm._postgresql_layer().to_dict()
        expected.pop("summary", "")
        expected.pop("description", "")
        # Check the plan is as expected
        self.assertEqual(plan.to_dict(), expected)
        self.assertEqual(self.harness.model.unit.status, ActiveStatus())
        container = self.harness.model.unit.get_container("postgresql")
        self.assertEqual(container.get_service("postgresql").is_running(), True)

        # Now test again with different config, knowing that the "postgresql"
        # service is running (because we've just tested it above), so we'll
        # be testing the `is_running() == True` codepath.
        self.harness.update_config({"postgres-password": "SECRET_PASSWORD"})
        plan = self.harness.get_container_pebble_plan("postgresql")
        # Adjust the expected plan
        expected["services"]["postgresql"]["environment"]["POSTGRES_PASSWORD"] = "SECRET_PASSWORD"
        self.assertEqual(plan.to_dict(), expected)
        self.assertEqual(container.get_service("postgresql").is_running(), True)
        self.assertEqual(self.harness.model.unit.status, ActiveStatus())
