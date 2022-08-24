# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import unittest
from unittest.mock import Mock, patch

from lightkube import codecs
from lightkube.resources.core_v1 import Pod
from ops.model import ActiveStatus
from ops.testing import Harness
from tenacity import RetryError

from charm import PostgresqlOperatorCharm
from constants import PEER
from tests.helpers import patch_network_get


class TestCharm(unittest.TestCase):
    @patch_network_get(private_address="1.1.1.1")
    def setUp(self):
        self._peer_relation = PEER
        self._postgresql_container = "postgresql"
        self._postgresql_service = "postgresql"

        self.harness = Harness(PostgresqlOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()
        self.charm = self.harness.charm
        self._context = {
            "namespace": self.harness.model.name,
            "app_name": self.harness.model.app.name,
        }

        self.rel_id = self.harness.add_relation(self._peer_relation, self.charm.app.name)

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.Patroni.render_postgresql_conf_file")
    def test_on_install(
        self,
        _render_postgresql_conf_file,
    ):
        self.charm.on.install.emit()
        _render_postgresql_conf_file.assert_called_once()

    @patch("charm.Patroni.reload_patroni_configuration")
    @patch("charm.Patroni.render_postgresql_conf_file")
    @patch("charm.PostgresqlOperatorCharm._patch_pod_labels")
    @patch("charm.PostgresqlOperatorCharm._create_resources")
    def test_on_leader_elected(self, _, __, _render_postgresql_conf_file, ___):
        # Assert that there is no password in the peer relation.
        self.assertIsNone(self.charm._peers.data[self.charm.app].get("postgres-password", None))
        self.assertIsNone(self.charm._peers.data[self.charm.app].get("replication-password", None))

        # Check that a new password was generated on leader election.
        self.harness.set_leader()
        superuser_password = self.charm._peers.data[self.charm.app].get("operator-password", None)
        self.assertIsNotNone(superuser_password)

        replication_password = self.charm._peers.data[self.charm.app].get(
            "replication-password", None
        )
        self.assertIsNotNone(replication_password)
        _render_postgresql_conf_file.assert_called_once()

        # Trigger a new leader election and check that the password is still the same.
        self.harness.set_leader(False)
        self.harness.set_leader()
        self.assertEqual(
            self.charm._peers.data[self.charm.app].get("operator-password", None),
            superuser_password,
        )
        self.assertEqual(
            self.charm._peers.data[self.charm.app].get("replication-password", None),
            replication_password,
        )

    @patch("charm.Patroni.reload_patroni_configuration")
    @patch("charm.Patroni.render_postgresql_conf_file")
    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.Patroni.member_started")
    @patch("charm.Patroni.render_patroni_yml_file")
    @patch("charm.PostgresqlOperatorCharm._patch_pod_labels")
    @patch("charm.PostgresqlOperatorCharm._create_resources")
    def test_on_postgresql_pebble_ready(
        self, _, __, _render_patroni_yml_file, _member_started, ___, ____
    ):
        # Check that the initial plan is empty.
        plan = self.harness.get_container_pebble_plan(self._postgresql_container)
        self.assertEqual(plan.to_dict(), {})

        # Get the current and the expected layer from the pebble plan and the _postgresql_layer
        # method, respectively.
        # TODO: test also replicas (DPE-398).
        self.harness.set_leader()
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
        _render_patroni_yml_file.assert_called_once()

    @patch("charm.PostgresqlOperatorCharm._get_operator_password")
    def test_on_get_operator_password(self, _get_operator_password):
        mock_event = Mock()
        _get_operator_password.return_value = "test-password"
        self.charm._on_get_operator_password(mock_event)
        _get_operator_password.assert_called_once()
        mock_event.set_results.assert_called_once_with({"operator-password": "test-password"})

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.Patroni.get_primary")
    def test_on_get_primary(self, _get_primary):
        mock_event = Mock()
        _get_primary.return_value = "postgresql-k8s-1"
        self.charm._on_get_primary(mock_event)
        _get_primary.assert_called_once()
        mock_event.set_results.assert_called_once_with({"primary": "postgresql-k8s-1"})

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.Patroni.get_primary")
    def test_fail_to_get_primary(self, _get_primary):
        mock_event = Mock()
        _get_primary.side_effect = [RetryError("fake error")]
        self.charm._on_get_primary(mock_event)
        _get_primary.assert_called_once()
        mock_event.set_results.assert_not_called()

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.Patroni.get_primary")
    def test_on_update_status(
        self,
        _get_primary,
    ):
        _get_primary.side_effect = [
            "postgresql-k8s/1",
            self.charm.unit.name,
        ]

        # Check primary message not being set (current unit is not the primary).
        self.charm.on.update_status.emit()
        _get_primary.assert_called_once()
        self.assertNotEqual(
            self.harness.model.unit.status,
            ActiveStatus("Primary"),
        )

        # Test again and check primary message being set (current unit is the primary).
        self.charm.on.update_status.emit()
        self.assertEqual(
            self.harness.model.unit.status,
            ActiveStatus("Primary"),
        )

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.Patroni.get_primary")
    def test_on_update_status_with_error_on_get_primary(self, _get_primary):
        _get_primary.side_effect = [RetryError("fake error")]

        with self.assertLogs("charm", "ERROR") as logs:
            self.charm.on.update_status.emit()
            self.assertIn(
                "ERROR:charm:failed to get primary with error RetryError[fake error]", logs.output
            )

    @patch("charm.PostgresqlOperatorCharm._patch_pod_labels")
    def test_on_upgrade_charm(self, _patch_pod_labels):
        self.charm.on.upgrade_charm.emit()
        _patch_pod_labels.assert_called_once()

    @patch("charm.Client")
    def test_create_resources(self, _client):
        self.charm._create_resources()
        with open("src/resources.yaml") as f:
            for obj in codecs.load_all_yaml(f, context=self._context):
                _client.return_value.create.assert_any_call(obj)

    @patch("charm.Client")
    def test_patch_pod_labels(self, _client):
        member = self.charm._unit.replace("/", "-")

        self.charm._patch_pod_labels(member)
        expected_patch = {
            "metadata": {
                "labels": {"application": "patroni", "cluster-name": f"patroni-{self.charm._name}"}
            }
        }
        _client.return_value.patch.assert_called_once_with(
            Pod,
            name=member,
            namespace=self.charm._namespace,
            obj=expected_patch,
        )

    @patch("charm.Patroni.reload_patroni_configuration")
    @patch("charm.Patroni.render_postgresql_conf_file")
    @patch("charm.PostgresqlOperatorCharm._patch_pod_labels")
    @patch("charm.PostgresqlOperatorCharm._create_resources")
    def test_postgresql_layer(self, _, __, ___, ____):
        # Test with the already generated password.
        self.harness.set_leader()
        plan = self.charm._postgresql_layer().to_dict()
        expected = {
            "summary": "postgresql + patroni layer",
            "description": "pebble config layer for postgresql + patroni",
            "services": {
                self._postgresql_service: {
                    "override": "replace",
                    "summary": "entrypoint of the postgresql + patroni image",
                    "command": "/usr/bin/python3 /usr/local/bin/patroni /var/lib/postgresql/data/patroni.yml",
                    "startup": "enabled",
                    "user": "postgres",
                    "group": "postgres",
                    "environment": {
                        "PATRONI_KUBERNETES_LABELS": f"{{application: patroni, cluster-name: patroni-{self.charm._name}}}",
                        "PATRONI_KUBERNETES_NAMESPACE": self.charm._namespace,
                        "PATRONI_KUBERNETES_USE_ENDPOINTS": "true",
                        "PATRONI_NAME": "postgresql-k8s-0",
                        "PATRONI_SCOPE": f"patroni-{self.charm._name}",
                        "PATRONI_REPLICATION_USERNAME": "replication",
                        "PATRONI_REPLICATION_PASSWORD": self.charm._replication_password,
                        "PATRONI_SUPERUSER_USERNAME": "operator",
                        "PATRONI_SUPERUSER_PASSWORD": self.charm._get_operator_password(),
                    },
                }
            },
        }
        self.assertDictEqual(plan, expected)

    @patch("charm.Patroni.reload_patroni_configuration")
    @patch("charm.Patroni.render_postgresql_conf_file")
    @patch("charm.PostgresqlOperatorCharm._patch_pod_labels")
    @patch("charm.PostgresqlOperatorCharm._create_resources")
    def test_get_operator_password(self, _, __, ___, ____):
        # Test for a None password.
        self.assertIsNone(self.charm._get_operator_password())

        # Then test for a non empty password after leader election and peer data set.
        self.harness.set_leader()
        password = self.charm._get_operator_password()
        self.assertIsNotNone(password)
        self.assertNotEqual(password, "")

    @patch("charm.Patroni.reload_patroni_configuration")
    @patch("charm.Patroni.render_postgresql_conf_file")
    @patch("charm.PostgresqlOperatorCharm._create_resources")
    def test_get_secret(self, _, __, ___):
        self.harness.set_leader()

        # Test application scope.
        assert self.charm._get_secret("app", "password") is None
        self.harness.update_relation_data(
            self.rel_id, self.charm.app.name, {"password": "test-password"}
        )
        assert self.charm._get_secret("app", "password") == "test-password"

        # Test unit scope.
        assert self.charm._get_secret("unit", "password") is None
        self.harness.update_relation_data(
            self.rel_id, self.charm.unit.name, {"password": "test-password"}
        )
        assert self.charm._get_secret("unit", "password") == "test-password"

    @patch("charm.Patroni.reload_patroni_configuration")
    @patch("charm.Patroni.render_postgresql_conf_file")
    @patch("charm.PostgresqlOperatorCharm._create_resources")
    def test_set_secret(self, _, __, ___):
        self.harness.set_leader()

        # Test application scope.
        assert "password" not in self.harness.get_relation_data(self.rel_id, self.charm.app.name)
        self.charm._set_secret("app", "password", "test-password")
        assert (
            self.harness.get_relation_data(self.rel_id, self.charm.app.name)["password"]
            == "test-password"
        )

        # Test unit scope.
        assert "password" not in self.harness.get_relation_data(self.rel_id, self.charm.unit.name)
        self.charm._set_secret("unit", "password", "test-password")
        assert (
            self.harness.get_relation_data(self.rel_id, self.charm.unit.name)["password"]
            == "test-password"
        )
