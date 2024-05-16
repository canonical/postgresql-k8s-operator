# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
import itertools
import json
import logging
from datetime import datetime
from unittest import TestCase
from unittest.mock import MagicMock, Mock, PropertyMock, patch

import pytest
from charms.postgresql_k8s.v0.postgresql import PostgreSQLUpdateUserPasswordError
from lightkube.resources.core_v1 import Endpoints, Pod, Service
from ops.model import (
    ActiveStatus,
    BlockedStatus,
    MaintenanceStatus,
    RelationDataTypeError,
    WaitingStatus,
)
from ops.pebble import Change, ChangeError, ChangeID, ServiceStatus
from ops.testing import Harness
from requests import ConnectionError
from tenacity import RetryError, wait_fixed

from charm import PostgresqlOperatorCharm
from constants import PEER, SECRET_INTERNAL_LABEL
from tests.helpers import patch_network_get
from tests.unit.helpers import _FakeApiError

POSTGRESQL_CONTAINER = "postgresql"
POSTGRESQL_SERVICE = "postgresql"
METRICS_SERVICE = "metrics_server"
PGBACKREST_SERVER_SERVICE = "pgbackrest server"

# used for assert functions
tc = TestCase()


@pytest.fixture(autouse=True)
def harness():
    with patch("charm.KubernetesServicePatch", lambda x, y: None):
        harness = Harness(PostgresqlOperatorCharm)
        harness.handle_exec("postgresql", ["locale", "-a"], result="C")

        harness.add_relation(PEER, "postgresql-k8s")
        harness.begin()
        yield harness
        harness.cleanup()


def test_on_leader_elected(harness):
    with (
        patch("charm.PostgresqlOperatorCharm._add_members"),
        patch("charm.Client") as _client,
        patch("charm.new_password", return_value="sekr1t"),
        patch("charm.PostgresqlOperatorCharm.get_secret", return_value=None) as _get_secret,
        patch("charm.PostgresqlOperatorCharm.set_secret") as _set_secret,
        patch("charm.Patroni.reload_patroni_configuration"),
        patch("charm.PostgresqlOperatorCharm._patch_pod_labels"),
        patch("charm.PostgresqlOperatorCharm._create_services"),
    ):
        rel_id = harness.model.get_relation(PEER).id
        # Check that a new password was generated on leader election and nothing is done
        # because the "leader" key is present in the endpoint annotations due to a scale
        # down to zero units.
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id, harness.charm.app.name, {"cluster_initialised": "True"}
            )
        _client.return_value.get.return_value = MagicMock(
            metadata=MagicMock(annotations=["leader"])
        )
        _client.return_value.list.side_effect = [
            [MagicMock(metadata=MagicMock(name="fakeName1", namespace="fakeNamespace"))],
            [MagicMock(metadata=MagicMock(name="fakeName2", namespace="fakeNamespace"))],
        ]
        harness.set_leader()
        assert _set_secret.call_count == 4
        _set_secret.assert_any_call("app", "operator-password", "sekr1t")
        _set_secret.assert_any_call("app", "replication-password", "sekr1t")
        _set_secret.assert_any_call("app", "rewind-password", "sekr1t")
        _set_secret.assert_any_call("app", "monitoring-password", "sekr1t")
        _client.return_value.get.assert_called_once_with(
            Endpoints, name=f"patroni-{harness.charm.app.name}", namespace=harness.charm.model.name
        )
        _client.return_value.patch.assert_not_called()
        tc.assertIn("cluster_initialised", harness.get_relation_data(rel_id, harness.charm.app))

        # Trigger a new leader election and check that the password is still the same, and that the charm
        # fixes the missing "leader" key in the endpoint annotations.
        _client.reset_mock()
        _client.return_value.get.return_value = MagicMock(metadata=MagicMock(annotations=[]))
        _set_secret.reset_mock()
        _get_secret.return_value = "test"
        harness.set_leader(False)
        harness.set_leader()
        assert _set_secret.call_count == 0
        _client.return_value.get.assert_called_once_with(
            Endpoints, name=f"patroni-{harness.charm.app.name}", namespace=harness.charm.model.name
        )
        _client.return_value.patch.assert_called_once_with(
            Endpoints,
            name=f"patroni-{harness.charm.app.name}",
            namespace=harness.charm.model.name,
            obj={"metadata": {"annotations": {"leader": "postgresql-k8s-0"}}},
        )
        tc.assertNotIn("cluster_initialised", harness.get_relation_data(rel_id, harness.charm.app))

        # Test a failure in fixing the "leader" key in the endpoint annotations.
        _client.return_value.patch.side_effect = _FakeApiError
        with tc.assertRaises(_FakeApiError):
            harness.set_leader(False)
            harness.set_leader()

        # Test no failure if the resource doesn't exist.
        _client.return_value.patch.side_effect = _FakeApiError(404)
        harness.set_leader(False)
        harness.set_leader()


@patch_network_get(private_address="1.1.1.1")
def test_on_postgresql_pebble_ready(harness):
    with (
        patch("charm.PostgresqlOperatorCharm._set_active_status") as _set_active_status,
        patch(
            "charm.Patroni.rock_postgresql_version", new_callable=PropertyMock
        ) as _rock_postgresql_version,
        patch(
            "charm.Patroni.primary_endpoint_ready", new_callable=PropertyMock
        ) as _primary_endpoint_ready,
        patch(
            "charm.PostgresqlOperatorCharm.enable_disable_extensions"
        ) as _enable_disable_extensions,
        patch("charm.PostgresqlOperatorCharm.update_config"),
        patch("charm.PostgresqlOperatorCharm.postgresql") as _postgresql,
        patch(
            "charm.PostgresqlOperatorCharm._create_services",
            side_effect=[None, _FakeApiError, None],
        ) as _create_services,
        patch("charm.Patroni.member_started") as _member_started,
        patch(
            "charm.PostgresqlOperatorCharm.push_tls_files_to_workload"
        ) as _push_tls_files_to_workload,
        patch("charm.PostgresqlOperatorCharm._patch_pod_labels"),
        patch("charm.PostgresqlOperatorCharm._on_leader_elected"),
        patch("charm.PostgresqlOperatorCharm._create_pgdata") as _create_pgdata,
    ):
        _rock_postgresql_version.return_value = "14.7"

        # Mock the primary endpoint ready property values.
        _primary_endpoint_ready.side_effect = [False, True]

        # Check that the initial plan is empty.
        harness.set_can_connect(POSTGRESQL_CONTAINER, True)
        plan = harness.get_container_pebble_plan(POSTGRESQL_CONTAINER)
        tc.assertEqual(plan.to_dict(), {})

        # Get the current and the expected layer from the pebble plan and the _postgresql_layer
        # method, respectively.
        # TODO: test also replicas (DPE-398).
        harness.set_leader()

        # Check for a Waiting status when the primary k8s endpoint is not ready yet.
        harness.container_pebble_ready(POSTGRESQL_CONTAINER)
        _create_pgdata.assert_called_once()
        tc.assertTrue(isinstance(harness.model.unit.status, WaitingStatus))
        _set_active_status.assert_not_called()

        # Check for a Blocked status when a failure happens .
        harness.container_pebble_ready(POSTGRESQL_CONTAINER)
        tc.assertTrue(isinstance(harness.model.unit.status, BlockedStatus))
        _set_active_status.assert_not_called()

        # Check for the Active status.
        _push_tls_files_to_workload.reset_mock()
        harness.container_pebble_ready(POSTGRESQL_CONTAINER)
        plan = harness.get_container_pebble_plan(POSTGRESQL_CONTAINER)
        expected = harness.charm._postgresql_layer().to_dict()
        expected.pop("summary", "")
        expected.pop("description", "")
        # Check the plan is as expected.
        tc.assertEqual(plan.to_dict(), expected)
        _set_active_status.assert_called_once()
        container = harness.model.unit.get_container(POSTGRESQL_CONTAINER)
        tc.assertEqual(container.get_service("postgresql").is_running(), True)
        _push_tls_files_to_workload.assert_called_once()


def test_on_postgresql_pebble_ready_no_connection(harness):
    with (
        patch(
            "charm.Patroni.rock_postgresql_version", new_callable=PropertyMock
        ) as _rock_postgresql_version,
        patch("charm.PostgresqlOperatorCharm._create_pgdata"),
    ):
        mock_event = MagicMock()
        mock_event.workload = harness.model.unit.get_container(POSTGRESQL_CONTAINER)
        _rock_postgresql_version.return_value = "14.7"

        harness.charm._on_postgresql_pebble_ready(mock_event)

        # Event was deferred and status is still maintenance
        mock_event.defer.assert_called_once()
        mock_event.set_results.assert_not_called()
        tc.assertIsInstance(harness.model.unit.status, MaintenanceStatus)


def test_on_get_password(harness):
    # Create a mock event and set passwords in peer relation data.
    harness.set_leader(True)
    mock_event = MagicMock(params={})
    rel_id = harness.model.get_relation(PEER).id
    harness.update_relation_data(
        rel_id,
        harness.charm.app.name,
        {
            "operator-password": "test-password",
            "replication-password": "replication-test-password",
        },
    )

    # Test providing an invalid username.
    mock_event.params["username"] = "user"
    harness.charm._on_get_password(mock_event)
    mock_event.fail.assert_called_once()
    mock_event.set_results.assert_not_called()

    # Test without providing the username option.
    mock_event.reset_mock()
    del mock_event.params["username"]
    harness.charm._on_get_password(mock_event)
    mock_event.set_results.assert_called_once_with({"password": "test-password"})

    # Also test providing the username option.
    mock_event.reset_mock()
    mock_event.params["username"] = "replication"
    harness.charm._on_get_password(mock_event)
    mock_event.set_results.assert_called_once_with({"password": "replication-test-password"})


def test_on_set_password(harness):
    with (
        patch("charm.Patroni.reload_patroni_configuration") as _reload_patroni_configuration,
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.PostgresqlOperatorCharm.set_secret") as _set_secret,
        patch("charm.PostgresqlOperatorCharm.postgresql") as _postgresql,
        patch("charm.Patroni.are_all_members_ready") as _are_all_members_ready,
        patch("charm.PostgresqlOperatorCharm._on_leader_elected"),
    ):
        # Create a mock event.
        mock_event = MagicMock(params={})

        # Set some values for the other mocks.
        _are_all_members_ready.side_effect = [False, True, True, True, True]
        _postgresql.update_user_password = PropertyMock(
            side_effect=[PostgreSQLUpdateUserPasswordError, None, None, None]
        )

        # Test trying to set a password through a non leader unit.
        harness.charm._on_set_password(mock_event)
        mock_event.fail.assert_called_once()
        _set_secret.assert_not_called()

        # Test providing an invalid username.
        harness.set_leader()
        mock_event.reset_mock()
        mock_event.params["username"] = "user"
        harness.charm._on_set_password(mock_event)
        mock_event.fail.assert_called_once()
        _set_secret.assert_not_called()

        # Test without providing the username option but without all cluster members ready.
        mock_event.reset_mock()
        del mock_event.params["username"]
        harness.charm._on_set_password(mock_event)
        mock_event.fail.assert_called_once()
        _set_secret.assert_not_called()

        # Test for an error updating when updating the user password in the database.
        mock_event.reset_mock()
        harness.charm._on_set_password(mock_event)
        mock_event.fail.assert_called_once()
        _set_secret.assert_not_called()

        # Test without providing the username option.
        harness.charm._on_set_password(mock_event)
        tc.assertEqual(_set_secret.call_args_list[0][0][1], "operator-password")

        # Also test providing the username option.
        _set_secret.reset_mock()
        mock_event.params["username"] = "replication"
        harness.charm._on_set_password(mock_event)
        tc.assertEqual(_set_secret.call_args_list[0][0][1], "replication-password")

        # And test providing both the username and password options.
        _set_secret.reset_mock()
        mock_event.params["password"] = "replication-test-password"
        harness.charm._on_set_password(mock_event)
        _set_secret.assert_called_once_with(
            "app", "replication-password", "replication-test-password"
        )


@patch_network_get(private_address="1.1.1.1")
def test_on_get_primary(harness):
    with patch("charm.Patroni.get_primary") as _get_primary:
        mock_event = Mock()
        _get_primary.return_value = "postgresql-k8s-1"
        harness.charm._on_get_primary(mock_event)
        _get_primary.assert_called_once()
        mock_event.set_results.assert_called_once_with({"primary": "postgresql-k8s-1"})


@patch_network_get(private_address="1.1.1.1")
def test_fail_to_get_primary(harness):
    with patch("charm.Patroni.get_primary") as _get_primary:
        mock_event = Mock()
        _get_primary.side_effect = [RetryError("fake error")]
        harness.charm._on_get_primary(mock_event)
        _get_primary.assert_called_once()
        mock_event.set_results.assert_not_called()


@patch_network_get(private_address="1.1.1.1")
def test_on_update_status(harness):
    with (
        patch(
            "charm.PostgresqlOperatorCharm._handle_processes_failures"
        ) as _handle_processes_failures,
        patch("charm.Patroni.member_started") as _member_started,
        patch("charm.Patroni.get_primary") as _get_primary,
        patch("ops.model.Container.pebble") as _pebble,
        patch("upgrade.PostgreSQLUpgrade.idle", return_value="idle"),
    ):
        # Test before the PostgreSQL service is available.
        _pebble.get_services.return_value = []
        harness.set_can_connect(POSTGRESQL_CONTAINER, True)
        harness.charm.on.update_status.emit()
        _get_primary.assert_not_called()

        # Test when a failure need to be handled.
        _pebble.get_services.return_value = ["service data"]
        _handle_processes_failures.return_value = True
        harness.charm.on.update_status.emit()
        _get_primary.assert_not_called()

        # Check primary message not being set (current unit is not the primary).
        _handle_processes_failures.return_value = False
        _get_primary.side_effect = [
            "postgresql-k8s/1",
            harness.charm.unit.name,
        ]
        harness.charm.on.update_status.emit()
        _get_primary.assert_called_once()
        tc.assertNotEqual(
            harness.model.unit.status,
            ActiveStatus("Primary"),
        )

        # Test again and check primary message being set (current unit is the primary).
        harness.charm.on.update_status.emit()
        tc.assertEqual(
            harness.model.unit.status,
            ActiveStatus("Primary"),
        )


def test_on_update_status_no_connection(harness):
    with (
        patch("charm.Patroni.get_primary") as _get_primary,
        patch("ops.model.Container.pebble") as _pebble,
    ):
        harness.charm.on.update_status.emit()

        # Exits before calling anything.
        _pebble.get_services.assert_not_called()
        _get_primary.assert_not_called()


@patch_network_get(private_address="1.1.1.1")
def test_on_update_status_with_error_on_get_primary(harness):
    with (
        patch(
            "charm.PostgresqlOperatorCharm._handle_processes_failures", return_value=False
        ) as _handle_processes_failures,
        patch("charm.Patroni.member_started") as _member_started,
        patch("charm.Patroni.get_primary") as _get_primary,
        patch("ops.model.Container.pebble") as _pebble,
        patch("upgrade.PostgreSQLUpgrade.idle", return_value=True),
    ):
        # Mock the access to the list of Pebble services.
        _pebble.get_services.return_value = ["service data"]

        _get_primary.side_effect = [RetryError("fake error")]

        harness.set_can_connect(POSTGRESQL_CONTAINER, True)

        with tc.assertLogs("charm", "ERROR") as logs:
            harness.charm.on.update_status.emit()
            tc.assertIn(
                "ERROR:charm:failed to get primary with error RetryError[fake error]", logs.output
            )


def test_on_update_status_after_restore_operation(harness):
    with (
        patch("charm.PostgresqlOperatorCharm._set_active_status") as _set_active_status,
        patch(
            "charm.PostgresqlOperatorCharm._handle_processes_failures"
        ) as _handle_processes_failures,
        patch("charm.PostgreSQLBackups.can_use_s3_repository") as _can_use_s3_repository,
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.Patroni.member_started", new_callable=PropertyMock) as _member_started,
        patch("ops.model.Container.pebble") as _pebble,
        patch("upgrade.PostgreSQLUpgrade.idle", return_value=True),
    ):
        rel_id = harness.model.get_relation(PEER).id
        # Mock the access to the list of Pebble services to test a failed restore.
        _pebble.get_services.return_value = [MagicMock(current=ServiceStatus.INACTIVE)]

        # Test when the restore operation fails.
        with harness.hooks_disabled():
            harness.set_leader()
            harness.update_relation_data(
                rel_id,
                harness.charm.app.name,
                {"restoring-backup": "2023-01-01T09:00:00Z"},
            )
        harness.set_can_connect(POSTGRESQL_CONTAINER, True)
        harness.charm.on.update_status.emit()
        _update_config.assert_not_called()
        _handle_processes_failures.assert_not_called()
        _set_active_status.assert_not_called()
        tc.assertIsInstance(harness.charm.unit.status, BlockedStatus)

        # Test when the restore operation hasn't finished yet.
        harness.charm.unit.status = ActiveStatus()
        _pebble.get_services.return_value = [MagicMock(current=ServiceStatus.ACTIVE)]
        _member_started.return_value = False
        harness.charm.on.update_status.emit()
        _update_config.assert_not_called()
        _handle_processes_failures.assert_not_called()
        _set_active_status.assert_not_called()
        tc.assertIsInstance(harness.charm.unit.status, ActiveStatus)

        # Assert that the backup id is still in the application relation databag.
        tc.assertEqual(
            harness.get_relation_data(rel_id, harness.charm.app),
            {"restoring-backup": "2023-01-01T09:00:00Z"},
        )

        # Test when the restore operation finished successfully.
        _member_started.return_value = True
        _can_use_s3_repository.return_value = (True, None)
        _handle_processes_failures.return_value = False
        harness.charm.on.update_status.emit()
        _update_config.assert_called_once()
        _handle_processes_failures.assert_called_once()
        _set_active_status.assert_called_once()
        tc.assertIsInstance(harness.charm.unit.status, ActiveStatus)

        # Assert that the backup id is not in the application relation databag anymore.
        tc.assertEqual(harness.get_relation_data(rel_id, harness.charm.app), {})

        # Test when it's not possible to use the configured S3 repository.
        _update_config.reset_mock()
        _handle_processes_failures.reset_mock()
        _set_active_status.reset_mock()
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id,
                harness.charm.app.name,
                {"restoring-backup": "2023-01-01T09:00:00Z"},
            )
        _can_use_s3_repository.return_value = (False, "fake validation message")
        harness.charm.on.update_status.emit()
        _update_config.assert_called_once()
        _handle_processes_failures.assert_not_called()
        _set_active_status.assert_not_called()
        tc.assertIsInstance(harness.charm.unit.status, BlockedStatus)
        tc.assertEqual(harness.charm.unit.status.message, "fake validation message")

        # Assert that the backup id is not in the application relation databag anymore.
        tc.assertEqual(harness.get_relation_data(rel_id, harness.charm.app), {})


def test_on_upgrade_charm(harness):
    with (
        patch(
            "charms.data_platform_libs.v0.upgrade.DataUpgrade._upgrade_supported_check"
        ) as _upgrade_supported_check,
        patch(
            "charm.PostgresqlOperatorCharm._patch_pod_labels", side_effect=[_FakeApiError, None]
        ) as _patch_pod_labels,
        patch(
            "charm.PostgresqlOperatorCharm._create_services",
            side_effect=[_FakeApiError, None, None],
        ) as _create_services,
    ):
        # Test with a problem happening when trying to create the k8s resources.
        harness.charm.unit.status = ActiveStatus()
        harness.charm.on.upgrade_charm.emit()
        _create_services.assert_called_once()
        _patch_pod_labels.assert_not_called()
        tc.assertTrue(isinstance(harness.charm.unit.status, BlockedStatus))

        # Test a successful k8s resources creation, but unsuccessful pod patch operation.
        _create_services.reset_mock()
        harness.charm.unit.status = ActiveStatus()
        harness.charm.on.upgrade_charm.emit()
        _create_services.assert_called_once()
        _patch_pod_labels.assert_called_once()
        tc.assertTrue(isinstance(harness.charm.unit.status, BlockedStatus))

        # Test a successful k8s resources creation and the operation to patch the pod.
        _create_services.reset_mock()
        _patch_pod_labels.reset_mock()
        harness.charm.unit.status = ActiveStatus()
        harness.charm.on.upgrade_charm.emit()
        _create_services.assert_called_once()
        _patch_pod_labels.assert_called_once()
        tc.assertFalse(isinstance(harness.charm.unit.status, BlockedStatus))


def test_create_services(harness):
    with patch("charm.Client") as _client:
        # Test the successful creation of the resources.
        _client.return_value.get.return_value = MagicMock(
            metadata=MagicMock(ownerReferences="fakeOwnerReferences")
        )
        harness.charm._create_services()
        _client.return_value.get.assert_called_once_with(
            res=Pod, name="postgresql-k8s-0", namespace=harness.charm.model.name
        )
        tc.assertEqual(_client.return_value.apply.call_count, 2)

        # Test when the charm fails to get first pod info.
        _client.reset_mock()
        _client.return_value.get.side_effect = _FakeApiError
        with tc.assertRaises(_FakeApiError):
            harness.charm._create_services()
            _client.return_value.get.assert_called_once_with(
                res=Pod, name="postgresql-k8s-0", namespace=harness.charm.model.name
            )
            _client.return_value.apply.assert_not_called()

        # Test when the charm fails to create a k8s service.
        _client.return_value.get.return_value = MagicMock(
            metadata=MagicMock(ownerReferences="fakeOwnerReferences")
        )
        _client.return_value.apply.side_effect = [None, _FakeApiError]
        with tc.assertRaises(_FakeApiError):
            harness.charm._create_services()
            _client.return_value.get.assert_called_once_with(
                res=Pod, name="postgresql-k8s-0", namespace=harness.charm.model.name
            )
            tc.assertEqual(_client.return_value.apply.call_count, 2)


def test_patch_pod_labels(harness):
    with patch("charm.Client") as _client:
        member = harness.charm._unit.replace("/", "-")

        harness.charm._patch_pod_labels(member)
        expected_patch = {
            "metadata": {
                "labels": {
                    "application": "patroni",
                    "cluster-name": f"patroni-{harness.charm._name}",
                }
            }
        }
        _client.return_value.patch.assert_called_once_with(
            Pod,
            name=member,
            namespace=harness.charm._namespace,
            obj=expected_patch,
        )


def test_postgresql_layer(harness):
    with (
        patch("charm.Patroni.reload_patroni_configuration"),
        patch("charm.PostgresqlOperatorCharm._patch_pod_labels"),
        patch("charm.PostgresqlOperatorCharm._create_services"),
    ):
        # Test with the already generated password.
        harness.set_leader()
        plan = harness.charm._postgresql_layer().to_dict()
        expected = {
            "summary": "postgresql + patroni layer",
            "description": "pebble config layer for postgresql + patroni",
            "services": {
                POSTGRESQL_SERVICE: {
                    "override": "replace",
                    "summary": "entrypoint of the postgresql + patroni image",
                    "command": "patroni /var/lib/postgresql/data/patroni.yml",
                    "startup": "enabled",
                    "user": "postgres",
                    "group": "postgres",
                    "environment": {
                        "PATRONI_KUBERNETES_LABELS": f"{{application: patroni, cluster-name: patroni-{harness.charm._name}}}",
                        "PATRONI_KUBERNETES_NAMESPACE": harness.charm._namespace,
                        "PATRONI_KUBERNETES_USE_ENDPOINTS": "true",
                        "PATRONI_NAME": "postgresql-k8s-0",
                        "PATRONI_SCOPE": f"patroni-{harness.charm._name}",
                        "PATRONI_REPLICATION_USERNAME": "replication",
                        "PATRONI_SUPERUSER_USERNAME": "operator",
                    },
                },
                METRICS_SERVICE: {
                    "override": "replace",
                    "summary": "postgresql metrics exporter",
                    "command": "/start-exporter.sh",
                    "startup": "enabled",
                    "after": [POSTGRESQL_SERVICE],
                    "user": "postgres",
                    "group": "postgres",
                    "environment": {
                        "DATA_SOURCE_NAME": (
                            f"user=monitoring "
                            f"password={harness.charm.get_secret('app', 'monitoring-password')} "
                            "host=/var/run/postgresql port=5432 database=postgres"
                        ),
                    },
                },
                PGBACKREST_SERVER_SERVICE: {
                    "override": "replace",
                    "summary": "pgBackRest server",
                    "command": PGBACKREST_SERVER_SERVICE,
                    "startup": "disabled",
                    "user": "postgres",
                    "group": "postgres",
                },
            },
            "checks": {
                POSTGRESQL_SERVICE: {
                    "override": "replace",
                    "level": "ready",
                    "http": {
                        "url": "http://postgresql-k8s-0.postgresql-k8s-endpoints:8008/health",
                    },
                }
            },
        }
        tc.assertDictEqual(plan, expected)


def test_on_stop(harness):
    with patch("charm.Client") as _client:
        rel_id = harness.model.get_relation(PEER).id
        # Test a successful run of the hook.
        for planned_units, relation_data in {
            0: {},
            1: {"some-relation-data": "some-value"},
        }.items():
            harness.set_planned_units(planned_units)
            with harness.hooks_disabled():
                harness.update_relation_data(
                    rel_id,
                    harness.charm.unit.name,
                    {"some-relation-data": "some-value"},
                )
            with tc.assertNoLogs("charm", "ERROR"):
                _client.return_value.get.return_value = MagicMock(
                    metadata=MagicMock(ownerReferences="fakeOwnerReferences")
                )
                _client.return_value.list.side_effect = [
                    [MagicMock(metadata=MagicMock(name="fakeName1", namespace="fakeNamespace"))],
                    [MagicMock(metadata=MagicMock(name="fakeName2", namespace="fakeNamespace"))],
                ]
                harness.charm.on.stop.emit()
                _client.return_value.get.assert_called_once_with(
                    res=Pod, name="postgresql-k8s-0", namespace=harness.charm.model.name
                )
                for kind in [Endpoints, Service]:
                    _client.return_value.list.assert_any_call(
                        kind,
                        namespace=harness.charm.model.name,
                        labels={"app.juju.is/created-by": harness.charm.app.name},
                    )
                tc.assertEqual(_client.return_value.apply.call_count, 2)
                tc.assertEqual(
                    harness.get_relation_data(rel_id, harness.charm.unit), relation_data
                )
                _client.reset_mock()

        # Test when the charm fails to get first pod info.
        _client.return_value.get.side_effect = _FakeApiError
        with tc.assertLogs("charm", "ERROR") as logs:
            harness.charm.on.stop.emit()
            _client.return_value.get.assert_called_once_with(
                res=Pod, name="postgresql-k8s-0", namespace=harness.charm.model.name
            )
            _client.return_value.list.assert_not_called()
            _client.return_value.apply.assert_not_called()
            tc.assertIn("failed to get first pod info", "".join(logs.output))

        # Test when the charm fails to get the k8s resources created by the charm and Patroni.
        _client.return_value.get.side_effect = None
        _client.return_value.list.side_effect = [[], _FakeApiError]
        with tc.assertLogs("charm", "ERROR") as logs:
            harness.charm.on.stop.emit()
            for kind in [Endpoints, Service]:
                _client.return_value.list.assert_any_call(
                    kind,
                    namespace=harness.charm.model.name,
                    labels={"app.juju.is/created-by": harness.charm.app.name},
                )
            _client.return_value.apply.assert_not_called()
            tc.assertIn(
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
        with tc.assertLogs("charm", "ERROR") as logs:
            harness.charm.on.stop.emit()
            tc.assertEqual(_client.return_value.apply.call_count, 2)
            tc.assertIn("failed to patch k8s MagicMock", "".join(logs.output))


def test_client_relations(harness):
    # Test when the charm has no relations.
    tc.assertEqual(harness.charm.client_relations, [])

    # Test when the charm has some relations.
    harness.add_relation("database", "application")
    harness.add_relation("db", "legacy-application")
    harness.add_relation("db-admin", "legacy-admin-application")
    database_relation = harness.model.get_relation("database")
    db_relation = harness.model.get_relation("db")
    db_admin_relation = harness.model.get_relation("db-admin")
    tc.assertEqual(
        harness.charm.client_relations, [database_relation, db_relation, db_admin_relation]
    )


def test_validate_config_options(harness):
    with patch(
        "charm.PostgresqlOperatorCharm.postgresql", new_callable=PropertyMock
    ) as _charm_lib:
        harness.set_can_connect(POSTGRESQL_CONTAINER, True)
        _charm_lib.return_value.get_postgresql_text_search_configs.return_value = []
        _charm_lib.return_value.validate_date_style.return_value = []
        _charm_lib.return_value.get_postgresql_timezones.return_value = []

        # Test instance_default_text_search_config exception
        with harness.hooks_disabled():
            harness.update_config({"instance_default_text_search_config": "pg_catalog.test"})

        with tc.assertRaises(ValueError) as e:
            harness.charm._validate_config_options()
            assert (
                e.msg == "instance_default_text_search_config config option has an invalid value"
            )

        _charm_lib.return_value.get_postgresql_text_search_configs.assert_called_once_with()
        _charm_lib.return_value.get_postgresql_text_search_configs.return_value = [
            "pg_catalog.test"
        ]

        # Test request_date_style exception
        with harness.hooks_disabled():
            harness.update_config({"request_date_style": "ISO, TEST"})

        with tc.assertRaises(ValueError) as e:
            harness.charm._validate_config_options()
            assert e.msg == "request_date_style config option has an invalid value"

        _charm_lib.return_value.validate_date_style.assert_called_once_with("ISO, TEST")
        _charm_lib.return_value.validate_date_style.return_value = ["ISO, TEST"]

        # Test request_time_zone exception
        with harness.hooks_disabled():
            harness.update_config({"request_time_zone": "TEST_ZONE"})

        with tc.assertRaises(ValueError) as e:
            harness.charm._validate_config_options()
            assert e.msg == "request_time_zone config option has an invalid value"

        _charm_lib.return_value.get_postgresql_timezones.assert_called_once_with()
        _charm_lib.return_value.get_postgresql_timezones.return_value = ["TEST_ZONE"]

    #
    # Secrets
    #


def test_scope_obj(harness):
    assert harness.charm._scope_obj("app") == harness.charm.framework.model.app
    assert harness.charm._scope_obj("unit") == harness.charm.framework.model.unit
    assert harness.charm._scope_obj("test") is None


@patch_network_get(private_address="1.1.1.1")
def test_get_secret_from_databag(harness):
    """Asserts that get_secret method can read secrets from databag.

    This must be backwards-compatible so it runs on both juju2 and juju3.
    """
    with patch("charm.PostgresqlOperatorCharm._on_leader_elected"):
        rel_id = harness.model.get_relation(PEER).id
        # App level changes require leader privileges
        harness.set_leader()
        # Test application scope.
        assert harness.charm.get_secret("app", "operator_password") is None
        harness.update_relation_data(
            rel_id, harness.charm.app.name, {"operator_password": "test-password"}
        )
        assert harness.charm.get_secret("app", "operator_password") == "test-password"

        # Unit level changes don't require leader privileges
        harness.set_leader(False)
        # Test unit scope.
        assert harness.charm.get_secret("unit", "operator_password") is None
        harness.update_relation_data(
            rel_id, harness.charm.unit.name, {"operator_password": "test-password"}
        )
        assert harness.charm.get_secret("unit", "operator_password") == "test-password"


@patch_network_get(private_address="1.1.1.1")
def test_on_get_password_secrets(harness):
    with (
        patch("charm.PostgresqlOperatorCharm._on_leader_elected"),
    ):
        # Create a mock event and set passwords in peer relation data.
        harness.set_leader()
        mock_event = MagicMock(params={})
        harness.charm.set_secret("app", "operator-password", "test-password")
        harness.charm.set_secret("app", "replication-password", "replication-test-password")

        # Test providing an invalid username.
        mock_event.params["username"] = "user"
        harness.charm._on_get_password(mock_event)
        mock_event.fail.assert_called_once()
        mock_event.set_results.assert_not_called()

        # Test without providing the username option.
        mock_event.reset_mock()
        del mock_event.params["username"]
        harness.charm._on_get_password(mock_event)
        mock_event.set_results.assert_called_once_with({"password": "test-password"})

        # Also test providing the username option.
        mock_event.reset_mock()
        mock_event.params["username"] = "replication"
        harness.charm._on_get_password(mock_event)
        mock_event.set_results.assert_called_once_with({"password": "replication-test-password"})


@pytest.mark.parametrize("scope", [("app"), ("unit")])
@patch_network_get(private_address="1.1.1.1")
def test_get_secret_secrets(harness, scope):
    with (
        patch("charm.PostgresqlOperatorCharm._on_leader_elected"),
    ):
        harness.set_leader()

        assert harness.charm.get_secret(scope, "operator-password") is None
        harness.charm.set_secret(scope, "operator-password", "test-password")
        assert harness.charm.get_secret(scope, "operator-password") == "test-password"


@patch_network_get(private_address="1.1.1.1")
def test_set_secret_in_databag(harness, only_without_juju_secrets):
    """Asserts that set_secret method writes to relation databag.

    This is juju2 specific. In juju3, set_secret writes to juju secrets.
    """
    with patch("charm.PostgresqlOperatorCharm._on_leader_elected"):
        rel_id = harness.model.get_relation(PEER).id
        harness.set_leader()

        # Test application scope.
        assert "password" not in harness.get_relation_data(rel_id, harness.charm.app.name)
        harness.charm.set_secret("app", "password", "test-password")
        assert (
            harness.get_relation_data(rel_id, harness.charm.app.name)["password"]
            == "test-password"
        )
        harness.charm.set_secret("app", "password", None)
        assert "password" not in harness.get_relation_data(rel_id, harness.charm.app.name)

        # Test unit scope.
        assert "password" not in harness.get_relation_data(rel_id, harness.charm.unit.name)
        harness.charm.set_secret("unit", "password", "test-password")
        assert (
            harness.get_relation_data(rel_id, harness.charm.unit.name)["password"]
            == "test-password"
        )
        harness.charm.set_secret("unit", "password", None)
        assert "password" not in harness.get_relation_data(rel_id, harness.charm.unit.name)

        with tc.assertRaises(RuntimeError):
            harness.charm.set_secret("test", "password", "test")


@pytest.mark.parametrize("scope,is_leader", [("app", True), ("unit", True), ("unit", False)])
@patch_network_get(private_address="1.1.1.1")
def test_set_reset_new_secret(harness, scope, is_leader):
    """NOTE: currently ops.testing seems to allow for non-leader to set secrets too!"""
    with (
        patch("charm.PostgresqlOperatorCharm._on_leader_elected"),
    ):
        # App has to be leader, unit can be either
        harness.set_leader(is_leader)
        # Getting current password
        harness.charm.set_secret(scope, "new-secret", "bla")
        assert harness.charm.get_secret(scope, "new-secret") == "bla"

        # Reset new secret
        harness.charm.set_secret(scope, "new-secret", "blablabla")
        assert harness.charm.get_secret(scope, "new-secret") == "blablabla"

        # Set another new secret
        harness.charm.set_secret(scope, "new-secret2", "blablabla")
        assert harness.charm.get_secret(scope, "new-secret2") == "blablabla"


@pytest.mark.parametrize("scope,is_leader", [("app", True), ("unit", True), ("unit", False)])
@patch_network_get(private_address="1.1.1.1")
def test_invalid_secret(harness, scope, is_leader):
    with (
        patch("charm.PostgresqlOperatorCharm._on_leader_elected"),
    ):
        # App has to be leader, unit can be either
        harness.set_leader(is_leader)

        with tc.assertRaises((RelationDataTypeError, TypeError)):
            harness.charm.set_secret(scope, "somekey", 1)

        harness.charm.set_secret(scope, "somekey", "")
        assert harness.charm.get_secret(scope, "somekey") is None


@patch_network_get(private_address="1.1.1.1")
def test_delete_password(harness, juju_has_secrets, caplog):
    """NOTE: currently ops.testing seems to allow for non-leader to remove secrets too!"""
    with (
        patch("charm.PostgresqlOperatorCharm._on_leader_elected"),
    ):
        harness.set_leader(True)
        harness.charm.set_secret("app", "operator-password", "somepw")
        harness.charm.remove_secret("app", "operator-password")
        assert harness.charm.get_secret("app", "operator-password") is None

        harness.set_leader(False)
        harness.charm.set_secret("unit", "operator-password", "somesecret")
        harness.charm.remove_secret("unit", "operator-password")
        assert harness.charm.get_secret("unit", "operator-password") is None

        harness.set_leader(True)
        with caplog.at_level(logging.DEBUG):
            if juju_has_secrets:
                error_message = (
                    "Non-existing secret operator-password was attempted to be removed."
                )
            else:
                error_message = (
                    "Non-existing field 'operator-password' was attempted to be removed"
                )

            harness.charm.remove_secret("app", "operator-password")
            assert error_message in caplog.text

            harness.charm.remove_secret("unit", "operator-password")
            assert error_message in caplog.text

            harness.charm.remove_secret("app", "non-existing-secret")
            assert (
                "Non-existing field 'non-existing-secret' was attempted to be removed"
                in caplog.text
            )

            harness.charm.remove_secret("unit", "non-existing-secret")
            assert (
                "Non-existing field 'non-existing-secret' was attempted to be removed"
                in caplog.text
            )


@pytest.mark.parametrize("scope,is_leader", [("app", True), ("unit", True), ("unit", False)])
@patch_network_get(private_address="1.1.1.1")
def test_migration_from_databag(harness, only_with_juju_secrets, scope, is_leader):
    """Check if we're moving on to use secrets when live upgrade from databag to Secrets usage.

    Since it checks for a migration from databag to juju secrets, it's specific to juju3.
    """
    with (
        patch("charm.PostgresqlOperatorCharm._on_leader_elected"),
    ):
        rel_id = harness.model.get_relation(PEER).id
        # App has to be leader, unit can be either
        harness.set_leader(is_leader)

        # Getting current password
        entity = getattr(harness.charm, scope)
        harness.update_relation_data(rel_id, entity.name, {"operator_password": "bla"})
        assert harness.charm.get_secret(scope, "operator_password") == "bla"

        # Reset new secret
        harness.charm.set_secret(scope, "operator-password", "blablabla")
        assert harness.charm.model.get_secret(label=f"{PEER}.postgresql-k8s.{scope}")
        assert harness.charm.get_secret(scope, "operator-password") == "blablabla"
        assert "operator-password" not in harness.get_relation_data(
            rel_id, getattr(harness.charm, scope).name
        )


@pytest.mark.parametrize("scope,is_leader", [("app", True), ("unit", True), ("unit", False)])
@patch_network_get(private_address="1.1.1.1")
def test_migration_from_single_secret(harness, only_with_juju_secrets, scope, is_leader):
    """Check if we're moving on to use secrets when live upgrade from databag to Secrets usage.

    Since it checks for a migration from databag to juju secrets, it's specific to juju3.
    """
    with (
        patch("charm.PostgresqlOperatorCharm._on_leader_elected"),
    ):
        rel_id = harness.model.get_relation(PEER).id

        # App has to be leader, unit can be either
        harness.set_leader(is_leader)

        secret = harness.charm.app.add_secret({"operator-password": "bla"})

        # Getting current password
        entity = getattr(harness.charm, scope)
        harness.update_relation_data(rel_id, entity.name, {SECRET_INTERNAL_LABEL: secret.id})
        assert harness.charm.get_secret(scope, "operator-password") == "bla"

        # Reset new secret
        # Only the leader can set app secret content.
        with harness.hooks_disabled():
            harness.set_leader(True)
        harness.charm.set_secret(scope, "operator-password", "blablabla")
        with harness.hooks_disabled():
            harness.set_leader(is_leader)
        assert harness.charm.model.get_secret(label=f"{PEER}.postgresql-k8s.{scope}")
        assert harness.charm.get_secret(scope, "operator-password") == "blablabla"
        assert SECRET_INTERNAL_LABEL not in harness.get_relation_data(
            rel_id, getattr(harness.charm, scope).name
        )


def test_on_peer_relation_changed(harness):
    with (
        patch("charm.PostgresqlOperatorCharm._set_active_status") as _set_active_status,
        patch(
            "backups.PostgreSQLBackups.start_stop_pgbackrest_service"
        ) as _start_stop_pgbackrest_service,
        patch("backups.PostgreSQLBackups.check_stanza") as _check_stanza,
        patch("backups.PostgreSQLBackups.coordinate_stanza_fields") as _coordinate_stanza_fields,
        patch("charm.Patroni.reinitialize_postgresql") as _reinitialize_postgresql,
        patch(
            "charm.Patroni.member_replication_lag", new_callable=PropertyMock
        ) as _member_replication_lag,
        patch("charm.PostgresqlOperatorCharm.is_primary") as _is_primary,
        patch("charm.Patroni.member_started", new_callable=PropertyMock) as _member_started,
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.PostgresqlOperatorCharm._add_members") as _add_members,
        patch("ops.framework.EventBase.defer") as _defer,
    ):
        rel_id = harness.model.get_relation(PEER).id
        # Test when the cluster was not initialised yet.
        harness.set_can_connect(POSTGRESQL_CONTAINER, True)
        relation = harness.model.get_relation(PEER, rel_id)
        harness.charm.on.database_peers_relation_changed.emit(relation)
        _defer.assert_called_once()
        _add_members.assert_not_called()
        _update_config.assert_not_called()
        _coordinate_stanza_fields.assert_not_called()
        _check_stanza.assert_not_called()
        _start_stop_pgbackrest_service.assert_not_called()

        # Test when the cluster has already initialised, but the unit is not the leader and is not
        # part of the cluster yet.
        _defer.reset_mock()
        with harness.hooks_disabled():
            harness.update_relation_data(
                rel_id,
                harness.charm.app.name,
                {"cluster_initialised": "True"},
            )
        harness.charm.on.database_peers_relation_changed.emit(relation)
        _defer.assert_not_called()
        _add_members.assert_not_called()
        _update_config.assert_not_called()
        _coordinate_stanza_fields.assert_not_called()
        _check_stanza.assert_not_called()
        _start_stop_pgbackrest_service.assert_not_called()

        # Test when the unit is the leader.
        with harness.hooks_disabled():
            harness.set_leader()
        harness.charm.on.database_peers_relation_changed.emit(relation)
        _defer.assert_not_called()
        _add_members.assert_called_once()
        _update_config.assert_not_called()
        _coordinate_stanza_fields.assert_not_called()
        _check_stanza.assert_not_called()
        _start_stop_pgbackrest_service.assert_not_called()

        # Test when the unit is part of the cluster but the container
        # is not ready yet.
        harness.set_can_connect(POSTGRESQL_CONTAINER, False)
        with harness.hooks_disabled():
            unit_id = harness.charm.unit.name.split("/")[1]
            harness.update_relation_data(
                rel_id,
                harness.charm.app.name,
                {
                    "endpoints": json.dumps([
                        f"{harness.charm.app.name}-{unit_id}.{harness.charm.app.name}-endpoints"
                    ])
                },
            )
        harness.charm.on.database_peers_relation_changed.emit(relation)
        _defer.assert_not_called()
        _update_config.assert_not_called()
        _coordinate_stanza_fields.assert_not_called()
        _check_stanza.assert_not_called()
        _start_stop_pgbackrest_service.assert_not_called()

        # Test when the container is ready but Patroni hasn't started yet.
        harness.set_can_connect(POSTGRESQL_CONTAINER, True)
        _member_started.return_value = False
        harness.charm.on.database_peers_relation_changed.emit(relation)
        _defer.assert_called_once()
        _update_config.assert_called_once()
        _coordinate_stanza_fields.assert_not_called()
        _check_stanza.assert_not_called()
        _start_stop_pgbackrest_service.assert_not_called()

        # Test when Patroni has already started but this is a replica with a
        # huge or unknown lag.
        _member_started.return_value = True
        for values in itertools.product([True, False], ["0", "1000", "1001", "unknown"]):
            _set_active_status.reset_mock()
            _defer.reset_mock()
            _coordinate_stanza_fields.reset_mock()
            _check_stanza.reset_mock()
            _start_stop_pgbackrest_service.reset_mock()
            _is_primary.return_value = values[0]
            _member_replication_lag.return_value = values[1]
            harness.charm.unit.status = ActiveStatus()
            harness.charm.on.database_peers_relation_changed.emit(relation)
            if _is_primary.return_value == values[0] or int(values[1]) <= 1000:
                _defer.assert_not_called()
                _coordinate_stanza_fields.assert_called_once()
                _check_stanza.assert_called_once()
                _start_stop_pgbackrest_service.assert_called_once()
                _set_active_status.assert_called_once()
            else:
                _defer.assert_called_once()
                _coordinate_stanza_fields.assert_not_called()
                _check_stanza.assert_not_called()
                _start_stop_pgbackrest_service.assert_not_called()
                _set_active_status.assert_not_called()

        # Test the status not being changed when it was not possible to start
        # the pgBackRest service yet.
        _defer.reset_mock()
        _set_active_status.reset_mock()
        _is_primary.return_value = True
        _member_replication_lag.return_value = "0"
        _start_stop_pgbackrest_service.return_value = False
        harness.charm.unit.status = MaintenanceStatus()
        with harness.hooks_disabled():
            harness.update_relation_data(rel_id, harness.charm.unit.name, {"start-tls-server": ""})
        harness.charm.on.database_peers_relation_changed.emit(relation)
        tc.assertEqual(
            harness.get_relation_data(rel_id, harness.charm.unit),
            {"start-tls-server": "True"},
        )
        _defer.assert_called_once()
        tc.assertIsInstance(harness.charm.unit.status, MaintenanceStatus)
        _set_active_status.assert_not_called()

        # Test the status being changed when it was possible to start the
        # pgBackRest service.
        _defer.reset_mock()
        _start_stop_pgbackrest_service.return_value = True
        harness.charm.on.database_peers_relation_changed.emit(relation)
        tc.assertEqual(
            harness.get_relation_data(rel_id, harness.charm.unit),
            {},
        )
        _defer.assert_not_called()
        _set_active_status.assert_called_once()

        # Test that a blocked status is not overridden.
        _set_active_status.reset_mock()
        harness.charm.unit.status = BlockedStatus()
        harness.charm.on.database_peers_relation_changed.emit(relation)
        tc.assertIsInstance(harness.charm.unit.status, BlockedStatus)
        _set_active_status.assert_not_called()


def test_handle_processes_failures(harness):
    with (
        patch("charm.Patroni.reinitialize_postgresql") as _reinitialize_postgresql,
        patch("charm.Patroni.member_streaming", new_callable=PropertyMock) as _member_streaming,
        patch(
            "charm.PostgresqlOperatorCharm.is_standby_leader", new_callable=PropertyMock
        ) as _is_standby_leader,
        patch(
            "charm.PostgresqlOperatorCharm.is_primary", new_callable=PropertyMock
        ) as _is_primary,
        patch(
            "charm.Patroni.is_database_running", new_callable=PropertyMock
        ) as _is_database_running,
        patch("charm.Patroni.member_started", new_callable=PropertyMock) as _member_started,
        patch("ops.model.Container.restart") as _restart,
    ):
        # Test when there are no processes failures to handle.
        harness.set_can_connect(POSTGRESQL_CONTAINER, True)
        for values in itertools.product(
            [True, False], [True, False], [True, False], [True, False], [True, False]
        ):
            # Skip conditions that lead to handling a process failure.
            if (not values[0] and values[2]) or (not values[3] and values[1] and not values[4]):
                continue

            _member_started.side_effect = [values[0], values[1]]
            _is_database_running.return_value = values[2]
            _is_primary.return_value = values[3]
            _member_streaming.return_value = values[4]
            tc.assertFalse(harness.charm._handle_processes_failures())
            _restart.assert_not_called()
            _reinitialize_postgresql.assert_not_called()

        # Test when the Patroni process is not running.
        _is_database_running.return_value = True
        for values in itertools.product(
            [
                None,
                ChangeError(
                    err="fake error",
                    change=Change(
                        ChangeID("1"),
                        "fake kind",
                        "fake summary",
                        "fake status",
                        [],
                        True,
                        "fake error",
                        datetime.now(),
                        datetime.now(),
                    ),
                ),
            ],
            [True, False],
            [True, False],
            [True, False],
        ):
            _restart.reset_mock()
            _restart.side_effect = values[0]
            _is_primary.return_value = values[1]
            _member_started.side_effect = [False, values[2]]
            _member_streaming.return_value = values[3]
            harness.charm.unit.status = ActiveStatus()
            result = harness.charm._handle_processes_failures()
            tc.assertTrue(result) if values[0] is None else tc.assertFalse(result)
            tc.assertIsInstance(harness.charm.unit.status, ActiveStatus)
            _restart.assert_called_once_with("postgresql")
            _reinitialize_postgresql.assert_not_called()

        # Test when the unit is a replica and it's not streaming from primary.
        _restart.reset_mock()
        _is_primary.return_value = False
        _is_standby_leader.return_value = False
        _member_streaming.return_value = False
        for values in itertools.product(
            [None, RetryError(last_attempt=1)], [True, False], [True, False]
        ):
            # Skip the condition that lead to handling other process failure.
            if not values[1] and values[2]:
                continue

            _reinitialize_postgresql.reset_mock()
            _reinitialize_postgresql.side_effect = values[0]
            _member_started.side_effect = [values[1], True]
            _is_database_running.return_value = values[2]
            harness.charm.unit.status = ActiveStatus()
            result = harness.charm._handle_processes_failures()
            tc.assertTrue(result) if values[0] is None else tc.assertFalse(result)
            tc.assertIsInstance(
                harness.charm.unit.status, MaintenanceStatus if values[0] is None else ActiveStatus
            )
            _restart.assert_not_called()
            _reinitialize_postgresql.assert_called_once()


def test_update_config(harness):
    with (
        patch("ops.model.Container.get_plan") as _get_plan,
        patch(
            "charm.PostgresqlOperatorCharm._handle_postgresql_restart_need"
        ) as _handle_postgresql_restart_need,
        patch("charm.Patroni.bulk_update_parameters_controller_by_patroni"),
        patch("charm.Patroni.member_started", new_callable=PropertyMock) as _member_started,
        patch(
            "charm.PostgresqlOperatorCharm._is_workload_running", new_callable=PropertyMock
        ) as _is_workload_running,
        patch("charm.Patroni.render_patroni_yml_file") as _render_patroni_yml_file,
        patch("charm.PostgreSQLUpgrade") as _upgrade,
        patch(
            "charm.PostgresqlOperatorCharm.is_tls_enabled", new_callable=PropertyMock
        ) as _is_tls_enabled,
        patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock,
    ):
        rel_id = harness.model.get_relation(PEER).id
        # Mock some properties.
        harness.set_can_connect(POSTGRESQL_CONTAINER, True)
        harness.add_relation("upgrade", harness.charm.app.name)
        postgresql_mock.is_tls_enabled = PropertyMock(side_effect=[False, False, False, False])
        _is_workload_running.side_effect = [False, False, True, True, False, True]
        _member_started.side_effect = [True, True, False]
        postgresql_mock.build_postgresql_parameters.return_value = {"test": "test"}

        # Test when only one of the two config options for profile limit memory is set.
        harness.update_config({"profile-limit-memory": 1000})
        harness.charm.update_config()

        # Test when only one of the two config options for profile limit memory is set.
        harness.update_config({"profile_limit_memory": 1000}, unset={"profile-limit-memory"})
        harness.charm.update_config()

        # Test when the two config options for profile limit memory are set at the same time.
        _render_patroni_yml_file.reset_mock()
        harness.update_config({"profile-limit-memory": 1000})
        with tc.assertRaises(ValueError):
            harness.charm.update_config()

        # Test without TLS files available.
        harness.update_config(unset={"profile-limit-memory", "profile_limit_memory"})
        with harness.hooks_disabled():
            harness.update_relation_data(rel_id, harness.charm.unit.name, {"tls": ""})
        _is_tls_enabled.return_value = False
        harness.charm.update_config()
        _render_patroni_yml_file.assert_called_once_with(
            connectivity=True,
            is_creating_backup=False,
            enable_tls=False,
            is_no_sync_member=False,
            backup_id=None,
            stanza=None,
            restore_stanza=None,
            parameters={"test": "test"},
        )
        _handle_postgresql_restart_need.assert_called_once()
        tc.assertNotIn("tls", harness.get_relation_data(rel_id, harness.charm.unit.name))

        # Test with TLS files available.
        _handle_postgresql_restart_need.reset_mock()
        harness.update_relation_data(
            rel_id, harness.charm.unit.name, {"tls": ""}
        )  # Mock some data in the relation to test that it change.
        _is_tls_enabled.return_value = True
        _render_patroni_yml_file.reset_mock()
        harness.charm.update_config()
        _render_patroni_yml_file.assert_called_once_with(
            connectivity=True,
            is_creating_backup=False,
            enable_tls=True,
            is_no_sync_member=False,
            backup_id=None,
            stanza=None,
            restore_stanza=None,
            parameters={"test": "test"},
        )
        _handle_postgresql_restart_need.assert_called_once()
        tc.assertNotIn(
            "tls",
            harness.get_relation_data(
                rel_id, harness.charm.unit.name
            ),  # The "tls" flag is set in handle_postgresql_restart_need.
        )

        # Test with workload not running yet.
        harness.update_relation_data(
            rel_id, harness.charm.unit.name, {"tls": ""}
        )  # Mock some data in the relation to test that it change.
        _handle_postgresql_restart_need.reset_mock()
        harness.charm.update_config()
        _handle_postgresql_restart_need.assert_not_called()
        tc.assertEqual(
            harness.get_relation_data(rel_id, harness.charm.unit.name)["tls"], "enabled"
        )

        # Test with member not started yet.
        harness.update_relation_data(
            rel_id, harness.charm.unit.name, {"tls": ""}
        )  # Mock some data in the relation to test that it doesn't change.
        harness.charm.update_config()
        _handle_postgresql_restart_need.assert_not_called()
        tc.assertNotIn("tls", harness.get_relation_data(rel_id, harness.charm.unit.name))


def test_handle_postgresql_restart_need(harness):
    with (
        patch.object(PostgresqlOperatorCharm, "postgresql", Mock()) as postgresql_mock,
        patch("charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock") as _restart,
        patch("charm.PostgresqlOperatorCharm._generate_metrics_jobs") as _generate_metrics_jobs,
        patch("charm.wait_fixed", return_value=wait_fixed(0)),
        patch("charm.Patroni.reload_patroni_configuration") as _reload_patroni_configuration,
        patch(
            "charm.PostgresqlOperatorCharm.is_tls_enabled", new_callable=PropertyMock
        ) as _is_tls_enabled,
    ):
        rel_id = harness.model.get_relation(PEER).id
        for values in itertools.product([True, False], [True, False], [True, False]):
            _reload_patroni_configuration.reset_mock()
            _generate_metrics_jobs.reset_mock()
            _restart.reset_mock()
            with harness.hooks_disabled():
                harness.update_relation_data(rel_id, harness.charm.unit.name, {"tls": ""})

            _is_tls_enabled.return_value = values[0]
            postgresql_mock.is_tls_enabled = PropertyMock(return_value=values[1])
            postgresql_mock.is_restart_pending = PropertyMock(return_value=values[2])

            harness.charm._handle_postgresql_restart_need()
            _reload_patroni_configuration.assert_called_once()
            (
                tc.assertIn("tls", harness.get_relation_data(rel_id, harness.charm.unit))
                if values[0]
                else tc.assertNotIn("tls", harness.get_relation_data(rel_id, harness.charm.unit))
            )
            if (values[0] != values[1]) or values[2]:
                _generate_metrics_jobs.assert_called_once_with(values[0])
                _restart.assert_called_once()
            else:
                _generate_metrics_jobs.assert_not_called()
                _restart.assert_not_called()


def test_set_active_status(harness):
    with (
        patch("charm.Patroni.member_started", new_callable=PropertyMock) as _member_started,
        patch(
            "charm.PostgresqlOperatorCharm.is_standby_leader", new_callable=PropertyMock
        ) as _is_standby_leader,
        patch("charm.Patroni.get_primary") as _get_primary,
    ):
        for values in itertools.product(
            [
                RetryError(last_attempt=1),
                ConnectionError,
                harness.charm.unit.name,
                f"{harness.charm.app.name}/2",
            ],
            [
                RetryError(last_attempt=1),
                ConnectionError,
                True,
                False,
            ],
            [True, False],
        ):
            harness.charm.unit.status = MaintenanceStatus("fake status")
            _member_started.return_value = values[2]
            if isinstance(values[0], str):
                _get_primary.side_effect = None
                _get_primary.return_value = values[0]
                if values[0] != harness.charm.unit.name and not isinstance(values[1], bool):
                    _is_standby_leader.side_effect = values[1]
                    _is_standby_leader.return_value = None
                    harness.charm._set_active_status()
                    tc.assertIsInstance(harness.charm.unit.status, MaintenanceStatus)
                else:
                    _is_standby_leader.side_effect = None
                    _is_standby_leader.return_value = values[1]
                    harness.charm._set_active_status()
                    tc.assertIsInstance(
                        harness.charm.unit.status,
                        ActiveStatus
                        if values[0] == harness.charm.unit.name or values[1] or values[2]
                        else MaintenanceStatus,
                    )
                    tc.assertEqual(
                        harness.charm.unit.status.message,
                        "Primary"
                        if values[0] == harness.charm.unit.name
                        else (
                            "Standby Leader" if values[1] else ("" if values[2] else "fake status")
                        ),
                    )
            else:
                _get_primary.side_effect = values[0]
                _get_primary.return_value = None
                harness.charm._set_active_status()
                tc.assertIsInstance(harness.charm.unit.status, MaintenanceStatus)
