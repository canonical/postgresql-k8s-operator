# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import unittest
from unittest.mock import MagicMock, Mock, PropertyMock, patch

from charms.postgresql_k8s.v0.postgresql import PostgreSQLUpdateUserPasswordError
from lightkube.resources.core_v1 import Endpoints, Pod, Service
from ops.model import (
    ActiveStatus,
    BlockedStatus,
    MaintenanceStatus,
    SecretNotFoundError,
    WaitingStatus,
)
from ops.pebble import ServiceStatus
from ops.testing import Harness
from tenacity import RetryError

from charm import PostgresqlOperatorCharm
from constants import (
    PEER,
    SECRET_CACHE_LABEL,
    SECRET_DELETED_LABEL,
    SECRET_INTERNAL_LABEL,
    SECRET_LABEL,
)
from tests.helpers import patch_network_get
from tests.unit.helpers import _FakeApiError


class TestCharm(unittest.TestCase):
    @patch("charm.KubernetesServicePatch", lambda x, y: None)
    @patch_network_get(private_address="1.1.1.1")
    def setUp(self):
        self._peer_relation = PEER
        self._postgresql_container = "postgresql"
        self._postgresql_service = "postgresql"
        self._metrics_service = "metrics_server"
        self.pgbackrest_server_service = "pgbackrest server"

        self.harness = Harness(PostgresqlOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()
        self.charm = self.harness.charm
        self._context = {
            "namespace": self.harness.model.name,
            "app_name": self.harness.model.app.name,
        }

        self.rel_id = self.harness.add_relation(self._peer_relation, self.charm.app.name)

    @patch("charm.new_password", return_value="sekr1t")
    @patch("charm.PostgresqlOperatorCharm.get_secret", return_value=None)
    @patch("charm.PostgresqlOperatorCharm.set_secret")
    @patch("charm.Patroni.reload_patroni_configuration")
    @patch("charm.PostgresqlOperatorCharm._patch_pod_labels")
    @patch("charm.PostgresqlOperatorCharm._create_services")
    def test_on_leader_elected(self, _, __, ___, _set_secret, _get_secret, _____):
        # Check that a new password was generated on leader election.
        self.harness.set_leader()
        assert _set_secret.call_count == 4
        _set_secret.assert_any_call("app", "operator-password", "sekr1t")
        _set_secret.assert_any_call("app", "replication-password", "sekr1t")
        _set_secret.assert_any_call("app", "rewind-password", "sekr1t")
        _set_secret.assert_any_call("app", "monitoring-password", "sekr1t")

        # Trigger a new leader election and check that the password is still the same.
        _set_secret.reset_mock()
        _get_secret.return_value = "test"
        self.harness.set_leader(False)
        self.harness.set_leader()
        assert _set_secret.call_count == 0

    @patch("charm.Patroni.rock_postgresql_version", new_callable=PropertyMock)
    @patch("charm.Patroni.primary_endpoint_ready", new_callable=PropertyMock)
    @patch("charm.PostgresqlOperatorCharm.update_config")
    @patch("charm.PostgresqlOperatorCharm.postgresql")
    @patch(
        "charm.PostgresqlOperatorCharm._create_services", side_effect=[None, _FakeApiError, None]
    )
    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.Patroni.member_started")
    @patch("charm.PostgresqlOperatorCharm.push_tls_files_to_workload")
    @patch("charm.PostgresqlOperatorCharm._patch_pod_labels")
    @patch("charm.PostgresqlOperatorCharm._on_leader_elected")
    @patch("charm.PostgresqlOperatorCharm._create_pgdata")
    def test_on_postgresql_pebble_ready(
        self,
        _create_pgdata,
        _,
        __,
        _push_tls_files_to_workload,
        _member_started,
        _create_services,
        _postgresql,
        ___,
        _primary_endpoint_ready,
        _rock_postgresql_version,
    ):
        _rock_postgresql_version.return_value = "14.7"

        # Mock the primary endpoint ready property values.
        _primary_endpoint_ready.side_effect = [False, True]

        # Check that the initial plan is empty.
        self.harness.set_can_connect(self._postgresql_container, True)
        plan = self.harness.get_container_pebble_plan(self._postgresql_container)
        self.assertEqual(plan.to_dict(), {})

        # Get the current and the expected layer from the pebble plan and the _postgresql_layer
        # method, respectively.
        # TODO: test also replicas (DPE-398).
        self.harness.set_leader()

        # Check for a Waiting status when the primary k8s endpoint is not ready yet.
        self.harness.container_pebble_ready(self._postgresql_container)
        _create_pgdata.assert_called_once()
        self.assertTrue(isinstance(self.harness.model.unit.status, WaitingStatus))

        # Check for a Blocked status when a failure happens .
        self.harness.container_pebble_ready(self._postgresql_container)
        self.assertTrue(isinstance(self.harness.model.unit.status, BlockedStatus))

        # Check for the Active status.
        _push_tls_files_to_workload.reset_mock()
        self.harness.container_pebble_ready(self._postgresql_container)
        plan = self.harness.get_container_pebble_plan(self._postgresql_container)
        expected = self.charm._postgresql_layer().to_dict()
        expected.pop("summary", "")
        expected.pop("description", "")
        expected.pop("checks", "")
        # Check the plan is as expected.
        self.assertEqual(plan.to_dict(), expected)
        self.assertEqual(self.harness.model.unit.status, ActiveStatus())
        container = self.harness.model.unit.get_container(self._postgresql_container)
        self.assertEqual(container.get_service(self._postgresql_service).is_running(), True)
        _push_tls_files_to_workload.assert_called_once()

    @patch("charm.Patroni.rock_postgresql_version", new_callable=PropertyMock)
    @patch("charm.PostgresqlOperatorCharm._create_pgdata")
    def test_on_postgresql_pebble_ready_no_connection(self, _, _rock_postgresql_version):
        mock_event = MagicMock()
        mock_event.workload = self.harness.model.unit.get_container(self._postgresql_container)
        _rock_postgresql_version.return_value = "14.7"

        self.charm._on_postgresql_pebble_ready(mock_event)

        # Event was deferred and status is still maintenance
        mock_event.defer.assert_called_once()
        mock_event.set_results.assert_not_called()
        self.assertIsInstance(self.harness.model.unit.status, MaintenanceStatus)

    def test_on_get_password(self):
        # Create a mock event and set passwords in peer relation data.
        mock_event = MagicMock(params={})
        self.harness.update_relation_data(
            self.rel_id,
            self.charm.app.name,
            {
                "operator-password": "test-password",
                "replication-password": "replication-test-password",
            },
        )

        # Test providing an invalid username.
        mock_event.params["username"] = "user"
        self.charm._on_get_password(mock_event)
        mock_event.fail.assert_called_once()
        mock_event.set_results.assert_not_called()

        # Test without providing the username option.
        mock_event.reset_mock()
        del mock_event.params["username"]
        self.charm._on_get_password(mock_event)
        mock_event.set_results.assert_called_once_with({"password": "test-password"})

        # Also test providing the username option.
        mock_event.reset_mock()
        mock_event.params["username"] = "replication"
        self.charm._on_get_password(mock_event)
        mock_event.set_results.assert_called_once_with({"password": "replication-test-password"})

    @patch("charm.Patroni.reload_patroni_configuration")
    @patch("charm.PostgresqlOperatorCharm.update_config")
    @patch("charm.PostgresqlOperatorCharm.set_secret")
    @patch("charm.PostgresqlOperatorCharm.postgresql")
    @patch("charm.Patroni.are_all_members_ready")
    @patch("charm.PostgresqlOperatorCharm._on_leader_elected")
    def test_on_set_password(
        self,
        _,
        _are_all_members_ready,
        _postgresql,
        _set_secret,
        _update_config,
        _reload_patroni_configuration,
    ):
        # Create a mock event.
        mock_event = MagicMock(params={})

        # Set some values for the other mocks.
        _are_all_members_ready.side_effect = [False, True, True, True, True]
        _postgresql.update_user_password = PropertyMock(
            side_effect=[PostgreSQLUpdateUserPasswordError, None, None, None]
        )

        # Test trying to set a password through a non leader unit.
        self.charm._on_set_password(mock_event)
        mock_event.fail.assert_called_once()
        _set_secret.assert_not_called()

        # Test providing an invalid username.
        self.harness.set_leader()
        mock_event.reset_mock()
        mock_event.params["username"] = "user"
        self.charm._on_set_password(mock_event)
        mock_event.fail.assert_called_once()
        _set_secret.assert_not_called()

        # Test without providing the username option but without all cluster members ready.
        mock_event.reset_mock()
        del mock_event.params["username"]
        self.charm._on_set_password(mock_event)
        mock_event.fail.assert_called_once()
        _set_secret.assert_not_called()

        # Test for an error updating when updating the user password in the database.
        mock_event.reset_mock()
        self.charm._on_set_password(mock_event)
        mock_event.fail.assert_called_once()
        _set_secret.assert_not_called()

        # Test without providing the username option.
        self.charm._on_set_password(mock_event)
        self.assertEqual(_set_secret.call_args_list[0][0][1], "operator-password")

        # Also test providing the username option.
        _set_secret.reset_mock()
        mock_event.params["username"] = "replication"
        self.charm._on_set_password(mock_event)
        self.assertEqual(_set_secret.call_args_list[0][0][1], "replication-password")

        # And test providing both the username and password options.
        _set_secret.reset_mock()
        mock_event.params["password"] = "replication-test-password"
        self.charm._on_set_password(mock_event)
        _set_secret.assert_called_once_with(
            "app", "replication-password", "replication-test-password"
        )

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
    @patch("charm.Patroni.member_started")
    @patch("charm.Patroni.get_primary")
    @patch("ops.model.Container.pebble")
    def test_on_update_status(
        self,
        _pebble,
        _get_primary,
        _member_started,
    ):
        # Mock the access to the list of Pebble services.
        _pebble.get_services.side_effect = [
            [],
            ["service data"],
            ["service data"],
        ]

        # Test before the PostgreSQL service is available.
        self.harness.set_can_connect(self._postgresql_container, True)
        self.charm.on.update_status.emit()
        _get_primary.assert_not_called()

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

    @patch("charm.Patroni.get_primary")
    @patch("ops.model.Container.pebble")
    def test_on_update_status_no_connection(self, _pebble, _get_primary):
        self.charm.on.update_status.emit()

        # Exits before calling anything.
        _pebble.get_services.assert_not_called()
        _get_primary.assert_not_called()

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.Patroni.member_started")
    @patch("charm.Patroni.get_primary")
    @patch("ops.model.Container.pebble")
    def test_on_update_status_with_error_on_get_primary(
        self, _pebble, _get_primary, _member_started
    ):
        # Mock the access to the list of Pebble services.
        _pebble.get_services.return_value = ["service data"]

        _get_primary.side_effect = [RetryError("fake error")]

        self.harness.set_can_connect(self._postgresql_container, True)

        with self.assertLogs("charm", "ERROR") as logs:
            self.charm.on.update_status.emit()
            self.assertIn(
                "ERROR:charm:failed to get primary with error RetryError[fake error]", logs.output
            )

    @patch("charm.PostgresqlOperatorCharm._set_primary_status_message")
    @patch("charm.PostgresqlOperatorCharm._handle_processes_failures")
    @patch("charm.PostgreSQLBackups.can_use_s3_repository")
    @patch("charm.PostgresqlOperatorCharm.update_config")
    @patch("charm.Patroni.member_started", new_callable=PropertyMock)
    @patch("ops.model.Container.pebble")
    def test_on_update_status_after_restore_operation(
        self,
        _pebble,
        _member_started,
        _update_config,
        _can_use_s3_repository,
        _handle_processes_failures,
        _set_primary_status_message,
    ):
        # Mock the access to the list of Pebble services to test a failed restore.
        _pebble.get_services.return_value = [MagicMock(current=ServiceStatus.INACTIVE)]

        # Test when the restore operation fails.
        with self.harness.hooks_disabled():
            self.harness.set_leader()
            self.harness.update_relation_data(
                self.rel_id,
                self.charm.app.name,
                {"restoring-backup": "2023-01-01T09:00:00Z"},
            )
        self.harness.set_can_connect(self._postgresql_container, True)
        self.charm.on.update_status.emit()
        _update_config.assert_not_called()
        _handle_processes_failures.assert_not_called()
        _set_primary_status_message.assert_not_called()
        self.assertIsInstance(self.charm.unit.status, BlockedStatus)

        # Test when the restore operation hasn't finished yet.
        self.charm.unit.status = ActiveStatus()
        _pebble.get_services.return_value = [MagicMock(current=ServiceStatus.ACTIVE)]
        _member_started.return_value = False
        self.charm.on.update_status.emit()
        _update_config.assert_not_called()
        _handle_processes_failures.assert_not_called()
        _set_primary_status_message.assert_not_called()
        self.assertIsInstance(self.charm.unit.status, ActiveStatus)

        # Assert that the backup id is still in the application relation databag.
        self.assertEqual(
            self.harness.get_relation_data(self.rel_id, self.charm.app),
            {"restoring-backup": "2023-01-01T09:00:00Z"},
        )

        # Test when the restore operation finished successfully.
        _member_started.return_value = True
        _can_use_s3_repository.return_value = (True, None)
        _handle_processes_failures.return_value = False
        self.charm.on.update_status.emit()
        _update_config.assert_called_once()
        _handle_processes_failures.assert_called_once()
        _set_primary_status_message.assert_called_once()
        self.assertIsInstance(self.charm.unit.status, ActiveStatus)

        # Assert that the backup id is not in the application relation databag anymore.
        self.assertEqual(self.harness.get_relation_data(self.rel_id, self.charm.app), {})

        # Test when it's not possible to use the configured S3 repository.
        _update_config.reset_mock()
        _handle_processes_failures.reset_mock()
        _set_primary_status_message.reset_mock()
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.rel_id,
                self.charm.app.name,
                {"restoring-backup": "2023-01-01T09:00:00Z"},
            )
        _can_use_s3_repository.return_value = (False, "fake validation message")
        self.charm.on.update_status.emit()
        _update_config.assert_called_once()
        _handle_processes_failures.assert_not_called()
        _set_primary_status_message.assert_not_called()
        self.assertIsInstance(self.charm.unit.status, BlockedStatus)
        self.assertEqual(self.charm.unit.status.message, "fake validation message")

        # Assert that the backup id is not in the application relation databag anymore.
        self.assertEqual(self.harness.get_relation_data(self.rel_id, self.charm.app), {})

    @patch("charms.data_platform_libs.v0.upgrade.DataUpgrade._upgrade_supported_check")
    @patch("charm.PostgresqlOperatorCharm._patch_pod_labels", side_effect=[_FakeApiError, None])
    @patch(
        "charm.PostgresqlOperatorCharm._create_services", side_effect=[_FakeApiError, None, None]
    )
    def test_on_upgrade_charm(self, _create_services, _patch_pod_labels, _upgrade_supported_check):
        # Test with a problem happening when trying to create the k8s resources.
        self.charm.unit.status = ActiveStatus()
        self.charm.on.upgrade_charm.emit()
        _create_services.assert_called_once()
        _patch_pod_labels.assert_not_called()
        self.assertTrue(isinstance(self.charm.unit.status, BlockedStatus))

        # Test a successful k8s resources creation, but unsuccessful pod patch operation.
        _create_services.reset_mock()
        self.charm.unit.status = ActiveStatus()
        self.charm.on.upgrade_charm.emit()
        _create_services.assert_called_once()
        _patch_pod_labels.assert_called_once()
        self.assertTrue(isinstance(self.charm.unit.status, BlockedStatus))

        # Test a successful k8s resources creation and the operation to patch the pod.
        _create_services.reset_mock()
        _patch_pod_labels.reset_mock()
        self.charm.unit.status = ActiveStatus()
        self.charm.on.upgrade_charm.emit()
        _create_services.assert_called_once()
        _patch_pod_labels.assert_called_once()
        self.assertFalse(isinstance(self.charm.unit.status, BlockedStatus))

    @patch("charm.Client")
    def test_create_services(self, _client):
        # Test the successful creation of the resources.
        _client.return_value.get.return_value = MagicMock(
            metadata=MagicMock(ownerReferences="fakeOwnerReferences")
        )
        self.charm._create_services()
        _client.return_value.get.assert_called_once_with(
            res=Pod, name="postgresql-k8s-0", namespace=self.charm.model.name
        )
        self.assertEqual(_client.return_value.apply.call_count, 2)

        # Test when the charm fails to get first pod info.
        _client.reset_mock()
        _client.return_value.get.side_effect = _FakeApiError
        with self.assertRaises(_FakeApiError):
            self.charm._create_services()
            _client.return_value.get.assert_called_once_with(
                res=Pod, name="postgresql-k8s-0", namespace=self.charm.model.name
            )
            _client.return_value.apply.assert_not_called()

        # Test when the charm fails to create a k8s service.
        _client.return_value.get.return_value = MagicMock(
            metadata=MagicMock(ownerReferences="fakeOwnerReferences")
        )
        _client.return_value.apply.side_effect = [None, _FakeApiError]
        with self.assertRaises(_FakeApiError):
            self.charm._create_services()
            _client.return_value.get.assert_called_once_with(
                res=Pod, name="postgresql-k8s-0", namespace=self.charm.model.name
            )
            self.assertEqual(_client.return_value.apply.call_count, 2)

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
    @patch("charm.PostgresqlOperatorCharm._patch_pod_labels")
    @patch("charm.PostgresqlOperatorCharm._create_services")
    def test_postgresql_layer(self, _, __, ___):
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
                    "command": "patroni /var/lib/postgresql/data/patroni.yml",
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
                        "PATRONI_SUPERUSER_USERNAME": "operator",
                    },
                },
                self._metrics_service: {
                    "override": "replace",
                    "summary": "postgresql metrics exporter",
                    "command": "/start-exporter.sh",
                    "startup": "enabled",
                    "after": [self._postgresql_service],
                    "user": "postgres",
                    "group": "postgres",
                    "environment": {
                        "DATA_SOURCE_NAME": (
                            f"user=monitoring "
                            f"password={self.charm.get_secret('app', 'monitoring-password')} "
                            "host=/var/run/postgresql port=5432 database=postgres"
                        ),
                    },
                },
                self.pgbackrest_server_service: {
                    "override": "replace",
                    "summary": "pgBackRest server",
                    "command": self.pgbackrest_server_service,
                    "startup": "disabled",
                    "user": "postgres",
                    "group": "postgres",
                },
            },
            "checks": {
                self._postgresql_service: {
                    "override": "replace",
                    "level": "ready",
                    "http": {
                        "url": "http://postgresql-k8s-0.postgresql-k8s-endpoints:8008/health",
                    },
                }
            },
        }
        self.assertDictEqual(plan, expected)

    def test_scope_obj(self):
        assert self.charm._scope_obj("app") == self.charm.framework.model.app
        assert self.charm._scope_obj("unit") == self.charm.framework.model.unit
        assert self.charm._scope_obj("test") is None

    @patch("charm.Patroni.reload_patroni_configuration")
    @patch("charm.PostgresqlOperatorCharm._create_services")
    def test_get_secret(self, _, __):
        self.harness.set_leader()

        # Test application scope.
        assert self.charm.get_secret("app", "password") is None
        self.harness.update_relation_data(
            self.rel_id, self.charm.app.name, {"password": "test-password"}
        )
        assert self.charm.get_secret("app", "password") == "test-password"

        # Test unit scope.
        assert self.charm.get_secret("unit", "password") is None
        self.harness.update_relation_data(
            self.rel_id, self.charm.unit.name, {"password": "test-password"}
        )
        assert self.charm.get_secret("unit", "password") == "test-password"

    @patch("ops.charm.model.Model.get_secret")
    @patch("charm.JujuVersion.has_secrets", new_callable=PropertyMock, return_value=True)
    @patch("charm.Patroni.reload_patroni_configuration")
    @patch("charm.PostgresqlOperatorCharm._create_services")
    def test_get_secret_juju_error(self, _, __, ___, _get_secret):
        self.harness.set_leader()
        _get_secret.return_value.get_content.return_value = {"password": "test-password"}

        # clean the caches
        if SECRET_INTERNAL_LABEL in self.charm.app_peer_data:
            del self.charm.app_peer_data[SECRET_INTERNAL_LABEL]
        self.charm.secrets["app"] = {}

        # general tests
        self.harness.update_relation_data(
            self.rel_id, self.charm.app.name, {SECRET_INTERNAL_LABEL: "secret_key"}
        )
        _get_secret.side_effect = SecretNotFoundError
        assert self.charm.get_secret("app", "password") is None
        self.harness.update_relation_data(self.rel_id, self.charm.app.name, {})

    @patch("ops.charm.model.Model.get_secret")
    @patch("charm.JujuVersion.has_secrets", new_callable=PropertyMock, return_value=True)
    @patch("charm.Patroni.reload_patroni_configuration")
    @patch("charm.PostgresqlOperatorCharm._create_services")
    def test_get_secret_juju(self, _, __, ___, _get_secret):
        self.harness.set_leader()
        _get_secret.return_value.get_content.return_value = {"password": "test-password"}

        # clean the caches
        if SECRET_INTERNAL_LABEL in self.charm.app_peer_data:
            del self.charm.app_peer_data[SECRET_INTERNAL_LABEL]
        self.charm.secrets["app"] = {}

        # Test application scope.
        assert self.charm.get_secret("app", "password") is None
        self.harness.update_relation_data(
            self.rel_id, self.charm.app.name, {SECRET_INTERNAL_LABEL: "secret_key"}
        )
        assert self.charm.get_secret("app", "password") == "test-password"
        _get_secret.assert_called_once_with(id="secret_key")

        _get_secret.reset_mock()

        # Test unit scope.
        assert self.charm.get_secret("unit", "password") is None
        self.harness.update_relation_data(
            self.rel_id, self.charm.unit.name, {SECRET_INTERNAL_LABEL: "secret_key"}
        )
        assert self.charm.get_secret("unit", "password") == "test-password"
        _get_secret.assert_called_once_with(id="secret_key")

    @patch("charm.Patroni.reload_patroni_configuration")
    @patch("charm.PostgresqlOperatorCharm._create_services")
    def test_set_secret(self, _, __):
        self.harness.set_leader()

        # Test application scope.
        assert "password" not in self.harness.get_relation_data(self.rel_id, self.charm.app.name)
        self.charm.set_secret("app", "password", "test-password")
        assert self.charm.get_secret("app", "password") == "test-password"
        self.charm.set_secret("app", "password", None)
        assert self.charm.get_secret("app", "password") is None

        # Test unit scope.
        assert "password" not in self.harness.get_relation_data(self.rel_id, self.charm.unit.name)
        self.charm.set_secret("unit", "password", "test-password")
        assert self.charm.get_secret("unit", "password") == "test-password"
        self.charm.set_secret("unit", "password", None)
        assert self.charm.get_secret("unit", "password") is None

    @patch("charm.JujuVersion.has_secrets", new_callable=PropertyMock, return_value=True)
    @patch("charm.Patroni.reload_patroni_configuration")
    @patch("charm.PostgresqlOperatorCharm._create_services")
    def test_set_secret_juju(self, _, __, ___):
        self.harness.set_leader()
        secret_mock = Mock()
        self.charm.secrets["app"][SECRET_LABEL] = secret_mock
        self.charm.secrets["unit"][SECRET_LABEL] = secret_mock
        self.charm.secrets["unit"][SECRET_CACHE_LABEL] = {}

        # Test application scope.
        assert "password" not in self.charm.secrets["app"].get(SECRET_CACHE_LABEL, {})
        self.charm.set_secret("app", "password", "test-password")
        assert self.charm.secrets["app"][SECRET_CACHE_LABEL]["password"] == "test-password"
        secret_mock.set_content.assert_called_once_with(
            self.charm.secrets["app"][SECRET_CACHE_LABEL]
        )
        secret_mock.reset_mock()

        self.charm.set_secret("app", "password", None)
        assert self.charm.secrets["app"][SECRET_CACHE_LABEL]["password"] == SECRET_DELETED_LABEL
        secret_mock.set_content.assert_called_once_with(
            self.charm.secrets["app"][SECRET_CACHE_LABEL]
        )
        secret_mock.reset_mock()

        # Test unit scope.
        assert "password" not in self.charm.secrets["unit"].get(SECRET_CACHE_LABEL, {})
        self.charm.set_secret("unit", "password", "test-password")
        assert self.charm.secrets["unit"][SECRET_CACHE_LABEL]["password"] == "test-password"
        secret_mock.set_content.assert_called_once_with(
            self.charm.secrets["unit"][SECRET_CACHE_LABEL]
        )
        secret_mock.reset_mock()

        self.charm.set_secret("unit", "password", None)
        assert self.charm.secrets["unit"][SECRET_CACHE_LABEL]["password"] == SECRET_DELETED_LABEL
        secret_mock.set_content.assert_called_once_with(
            self.charm.secrets["unit"][SECRET_CACHE_LABEL]
        )
        secret_mock.reset_mock()

    @patch("charm.Client")
    def test_on_stop(self, _client):
        # Test a successful run of the hook.
        with self.assertNoLogs("charm", "ERROR"):
            _client.return_value.get.return_value = MagicMock(
                metadata=MagicMock(ownerReferences="fakeOwnerReferences")
            )
            _client.return_value.list.side_effect = [
                [MagicMock(metadata=MagicMock(name="fakeName1", namespace="fakeNamespace"))],
                [MagicMock(metadata=MagicMock(name="fakeName2", namespace="fakeNamespace"))],
            ]
            self.charm.on.stop.emit()
            _client.return_value.get.assert_called_once_with(
                res=Pod, name="postgresql-k8s-0", namespace=self.charm.model.name
            )
            for kind in [Endpoints, Service]:
                _client.return_value.list.assert_any_call(
                    kind,
                    namespace=self.charm.model.name,
                    labels={"app.juju.is/created-by": self.charm.app.name},
                )
            self.assertEqual(_client.return_value.apply.call_count, 2)

        # Test when the charm fails to get first pod info.
        _client.reset_mock()
        _client.return_value.get.side_effect = _FakeApiError
        with self.assertLogs("charm", "ERROR") as logs:
            self.charm.on.stop.emit()
            _client.return_value.get.assert_called_once_with(
                res=Pod, name="postgresql-k8s-0", namespace=self.charm.model.name
            )
            _client.return_value.list.assert_not_called()
            _client.return_value.apply.assert_not_called()
            self.assertIn("failed to get first pod info", "".join(logs.output))

        # Test when the charm fails to get the k8s resources created by the charm and Patroni.
        _client.return_value.get.side_effect = None
        _client.return_value.list.side_effect = [[], _FakeApiError]
        with self.assertLogs("charm", "ERROR") as logs:
            self.charm.on.stop.emit()
            for kind in [Endpoints, Service]:
                _client.return_value.list.assert_any_call(
                    kind,
                    namespace=self.charm.model.name,
                    labels={"app.juju.is/created-by": self.charm.app.name},
                )
            _client.return_value.apply.assert_not_called()
            self.assertIn(
                "failed to get the k8s resources created by the charm and Patroni",
                "".join(logs.output),
            )

        # Test when the charm fails to patch a k8s resource.
        _client.return_value.get.return_value = MagicMock(
            metadata=MagicMock(ownerReferences="fakeOwnerReferences")
        )
        _client.return_value.list.side_effect = [
            [MagicMock(metadata=MagicMock(name="fakeName1", namespace="fakeNamespace"))],
            [MagicMock(metadata=MagicMock(name="fakeName2", namespace="fakeNamespace"))],
        ]
        _client.return_value.apply.side_effect = [None, _FakeApiError]
        with self.assertLogs("charm", "ERROR") as logs:
            self.charm.on.stop.emit()
            self.assertEqual(_client.return_value.apply.call_count, 2)
            self.assertIn("failed to patch k8s MagicMock", "".join(logs.output))
