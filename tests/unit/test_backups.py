# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import datetime
import unittest
from typing import OrderedDict
from unittest.mock import MagicMock, PropertyMock, call, mock_open, patch

from boto3.exceptions import S3UploadFailedError
from botocore.exceptions import ClientError
from jinja2 import Template
from ops import ActiveStatus, BlockedStatus, MaintenanceStatus, Unit
from ops.pebble import Change, ChangeError, ChangeID, ExecError
from ops.testing import Harness
from tenacity import RetryError, wait_fixed

from charm import PostgresqlOperatorCharm
from constants import PEER
from tests.unit.helpers import _FakeApiError

ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE = "the S3 repository has backups from another cluster"
FAILED_TO_ACCESS_CREATE_BUCKET_ERROR_MESSAGE = (
    "failed to access/create the bucket, check your S3 settings"
)
FAILED_TO_INITIALIZE_STANZA_ERROR_MESSAGE = "failed to initialize stanza, check your S3 settings"
S3_PARAMETERS_RELATION = "s3-parameters"


class TestPostgreSQLBackups(unittest.TestCase):
    @patch("charm.KubernetesServicePatch", lambda x, y: None)
    def setUp(self):
        # Mock generic sync client to avoid search to ~/.kube/config.
        self.patcher = patch("lightkube.core.client.GenericSyncClient")
        self.mock_k8s_client = self.patcher.start()

        self.harness = Harness(PostgresqlOperatorCharm)
        self.addCleanup(self.harness.cleanup)

        # Set up the initial relation and hooks.
        self.peer_rel_id = self.harness.add_relation(PEER, "postgresql-k8s")
        self.harness.add_relation_unit(self.peer_rel_id, "postgresql-k8s/0")
        self.harness.begin()
        self.charm = self.harness.charm

    def relate_to_s3_integrator(self):
        self.s3_rel_id = self.harness.add_relation(S3_PARAMETERS_RELATION, "s3-integrator")

    def remove_relation_from_s3_integrator(self):
        self.harness.remove_relation(S3_PARAMETERS_RELATION, "s3-integrator")
        self.s3_rel_id = None

    def test_stanza_name(self):
        self.assertEqual(
            self.charm.backup.stanza_name, f"{self.charm.model.name}.{self.charm.cluster_name}"
        )

    def test_are_backup_settings_ok(self):
        # Test without S3 relation.
        self.assertEqual(
            self.charm.backup._are_backup_settings_ok(),
            (False, "Relation with s3-integrator charm missing, cannot create/restore backup."),
        )

        # Test when there are missing S3 parameters.
        self.relate_to_s3_integrator()
        self.assertEqual(
            self.charm.backup._are_backup_settings_ok(),
            (False, "Missing S3 parameters: ['bucket', 'access-key', 'secret-key']"),
        )

        # Test when all required parameters are provided.
        with patch("charm.PostgreSQLBackups._retrieve_s3_parameters") as _retrieve_s3_parameters:
            _retrieve_s3_parameters.return_value = ["bucket", "access-key", "secret-key"], []
            self.assertEqual(
                self.charm.backup._are_backup_settings_ok(),
                (True, None),
            )

    @patch("charm.PostgreSQLBackups._are_backup_settings_ok")
    @patch("charm.Patroni.member_started", new_callable=PropertyMock)
    @patch("ops.model.Application.planned_units")
    @patch("charm.PostgresqlOperatorCharm.is_primary", new_callable=PropertyMock)
    def test_can_unit_perform_backup(
        self, _is_primary, _planned_units, _member_started, _are_backup_settings_ok
    ):
        # Test when the charm fails to retrieve the primary.
        _is_primary.side_effect = RetryError(last_attempt=1)
        self.assertEqual(
            self.charm.backup._can_unit_perform_backup(),
            (False, "Unit cannot perform backups as the database seems to be offline"),
        )

        # Test when the unit is in a blocked state.
        _is_primary.side_effect = None
        self.charm.unit.status = BlockedStatus("fake blocked state")
        self.assertEqual(
            self.charm.backup._can_unit_perform_backup(),
            (False, "Unit is in a blocking state"),
        )

        # Test when running the check in the primary, there are replicas and TLS is enabled.
        self.charm.unit.status = ActiveStatus()
        _is_primary.return_value = True
        _planned_units.return_value = 2
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.peer_rel_id,
                self.charm.unit.name,
                {"tls": "True"},
            )
        self.assertEqual(
            self.charm.backup._can_unit_perform_backup(),
            (False, "Unit cannot perform backups as it is the cluster primary"),
        )

        # Test when running the check in a replica and TLS is disabled.
        _is_primary.return_value = False
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.peer_rel_id,
                self.charm.unit.name,
                {"tls": ""},
            )
        self.assertEqual(
            self.charm.backup._can_unit_perform_backup(),
            (False, "Unit cannot perform backups as TLS is not enabled"),
        )

        # Test when Patroni or PostgreSQL hasn't started yet.
        _is_primary.return_value = True
        _member_started.return_value = False
        self.assertEqual(
            self.charm.backup._can_unit_perform_backup(),
            (False, "Unit cannot perform backups as it's not in running state"),
        )

        # Test when the stanza was not initialised yet.
        _member_started.return_value = True
        self.assertEqual(
            self.charm.backup._can_unit_perform_backup(),
            (False, "Stanza was not initialised"),
        )

        # Test when S3 parameters are not ok.
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.peer_rel_id,
                self.charm.app.name,
                {"stanza": self.charm.backup.stanza_name},
            )
        _are_backup_settings_ok.return_value = (False, "fake error message")
        self.assertEqual(
            self.charm.backup._can_unit_perform_backup(),
            (False, "fake error message"),
        )

        # Test when everything is ok to run a backup.
        _are_backup_settings_ok.return_value = (True, None)
        self.assertEqual(
            self.charm.backup._can_unit_perform_backup(),
            (True, None),
        )

    @patch("charm.Patroni.reload_patroni_configuration")
    @patch("charm.Patroni.member_started", new_callable=PropertyMock)
    @patch("charm.PostgresqlOperatorCharm.update_config")
    @patch(
        "charm.Patroni.rock_postgresql_version", new_callable=PropertyMock(return_value="14.10")
    )
    @patch("charm.PostgreSQLBackups._execute_command")
    def test_can_use_s3_repository(
        self,
        _execute_command,
        _rock_postgresql_version,
        _update_config,
        _member_started,
        _reload_patroni_configuration,
    ):
        # Define the stanza name inside the unit relation data.
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.peer_rel_id,
                self.charm.app.name,
                {"stanza": self.charm.backup.stanza_name},
            )

        # Test when nothing is returned from the pgBackRest info command.
        _execute_command.return_value = (None, None)
        self.assertEqual(
            self.charm.backup.can_use_s3_repository(),
            (False, FAILED_TO_INITIALIZE_STANZA_ERROR_MESSAGE),
        )

        # Test when the unit is a replica.
        pgbackrest_info_same_cluster_backup_output = (
            f'[{{"db": [{{"system-id": "12345"}}], "name": "{self.charm.backup.stanza_name}"}}]',
            None,
        )
        _execute_command.return_value = pgbackrest_info_same_cluster_backup_output
        self.assertEqual(
            self.charm.backup.can_use_s3_repository(),
            (True, None),
        )

        # Assert that the stanza name is still in the unit relation data.
        self.assertEqual(
            self.harness.get_relation_data(self.peer_rel_id, self.charm.app),
            {"stanza": self.charm.backup.stanza_name},
        )

        # Test when the unit is the leader and the workload is running,
        # but an exception happens when retrieving the cluster system id.
        _member_started.return_value = True
        _execute_command.side_effect = [
            pgbackrest_info_same_cluster_backup_output,
            ("", "fake error"),
        ]
        with self.harness.hooks_disabled():
            self.harness.set_leader()
        with self.assertRaises(Exception):
            self.assertEqual(
                self.charm.backup.can_use_s3_repository(),
                (False, ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE),
            )
        _update_config.assert_not_called()
        _member_started.assert_not_called()
        _reload_patroni_configuration.assert_not_called()

        # Test when the cluster system id can be retrieved, but it's different from the stanza system id.
        pgbackrest_info_other_cluster_system_id_backup_output = (
            f'[{{"db": [{{"system-id": "12345"}}], "name": "{self.charm.backup.stanza_name}"}}]',
            None,
        )
        other_instance_system_identifier_output = (
            "Database system identifier:           67890",
            "",
        )
        _execute_command.side_effect = [
            pgbackrest_info_other_cluster_system_id_backup_output,
            other_instance_system_identifier_output,
        ]
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.peer_rel_id,
                self.charm.app.name,
                {"stanza": self.charm.backup.stanza_name},
            )
        self.assertEqual(
            self.charm.backup.can_use_s3_repository(),
            (False, ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE),
        )
        _update_config.assert_called_once()
        _member_started.assert_called_once()
        _reload_patroni_configuration.assert_called_once()

        # Assert that the stanza name is not present in the unit relation data anymore.
        self.assertEqual(self.harness.get_relation_data(self.peer_rel_id, self.charm.app), {})

        # Test when the cluster system id can be retrieved, but it's different from the stanza system id.
        _update_config.reset_mock()
        _member_started.reset_mock()
        _reload_patroni_configuration.reset_mock()
        pgbackrest_info_other_cluster_name_backup_output = (
            f'[{{"db": [{{"system-id": "12345"}}], "name": "another-model.{self.charm.cluster_name}"}}]',
            None,
        )
        same_instance_system_identifier_output = (
            "Database system identifier:           12345",
            "",
        )
        _execute_command.side_effect = [
            pgbackrest_info_other_cluster_name_backup_output,
            same_instance_system_identifier_output,
        ]
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.peer_rel_id,
                self.charm.app.name,
                {"stanza": self.charm.backup.stanza_name},
            )
        self.assertEqual(
            self.charm.backup.can_use_s3_repository(),
            (False, ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE),
        )
        _update_config.assert_called_once()
        _member_started.assert_called_once()
        _reload_patroni_configuration.assert_called_once()

        # Assert that the stanza name is not present in the unit relation data anymore.
        self.assertEqual(self.harness.get_relation_data(self.peer_rel_id, self.charm.app), {})

        # Test when the workload is not running.
        _update_config.reset_mock()
        _member_started.reset_mock()
        _reload_patroni_configuration.reset_mock()
        _member_started.return_value = False
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.peer_rel_id,
                self.charm.app.name,
                {"stanza": self.charm.backup.stanza_name},
            )
        _execute_command.side_effect = [
            pgbackrest_info_same_cluster_backup_output,
            other_instance_system_identifier_output,
        ]
        self.assertEqual(
            self.charm.backup.can_use_s3_repository(),
            (False, ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE),
        )
        _update_config.assert_called_once()
        _member_started.assert_called_once()
        _reload_patroni_configuration.assert_not_called()

        # Assert that the stanza name is not present in the unit relation data anymore.
        self.assertEqual(self.harness.get_relation_data(self.peer_rel_id, self.charm.app), {})

        # Test when there is no backup from another cluster in the S3 repository.
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.peer_rel_id,
                self.charm.app.name,
                {"stanza": self.charm.backup.stanza_name},
            )
        _execute_command.side_effect = [
            pgbackrest_info_same_cluster_backup_output,
            same_instance_system_identifier_output,
        ]
        self.assertEqual(
            self.charm.backup.can_use_s3_repository(),
            (True, None),
        )

        # Assert that the stanza name is still in the unit relation data.
        self.assertEqual(
            self.harness.get_relation_data(self.peer_rel_id, self.charm.app),
            {"stanza": self.charm.backup.stanza_name},
        )

    def test_construct_endpoint(self):
        # Test with an AWS endpoint without region.
        s3_parameters = {"endpoint": "https://s3.amazonaws.com", "region": ""}
        self.assertEqual(
            self.charm.backup._construct_endpoint(s3_parameters), "https://s3.amazonaws.com"
        )

        # Test with an AWS endpoint with region.
        s3_parameters["region"] = "us-east-1"
        self.assertEqual(
            self.charm.backup._construct_endpoint(s3_parameters),
            "https://s3.us-east-1.amazonaws.com",
        )

        # Test with another cloud endpoint.
        s3_parameters["endpoint"] = "https://storage.googleapis.com"
        self.assertEqual(
            self.charm.backup._construct_endpoint(s3_parameters), "https://storage.googleapis.com"
        )

    @patch("boto3.session.Session.resource")
    @patch("charm.PostgreSQLBackups._retrieve_s3_parameters")
    def test_create_bucket_if_not_exists(self, _retrieve_s3_parameters, _resource):
        # Test when there are missing S3 parameters.
        _retrieve_s3_parameters.return_value = ([], ["bucket", "access-key", "secret-key"])
        self.charm.backup._create_bucket_if_not_exists()
        _resource.assert_not_called()

        # Test when the charm fails to create a boto3 session.
        _retrieve_s3_parameters.return_value = (
            {
                "bucket": "test-bucket",
                "access-key": "test-access-key",
                "secret-key": "test-secret-key",
                "endpoint": "test-endpoint",
                "region": "test-region",
            },
            [],
        )
        _resource.side_effect = ValueError
        with self.assertRaises(ValueError):
            self.charm.backup._create_bucket_if_not_exists()

        # Test when the bucket already exists.
        _resource.side_effect = None
        head_bucket = _resource.return_value.Bucket.return_value.meta.client.head_bucket
        create = _resource.return_value.Bucket.return_value.create
        wait_until_exists = _resource.return_value.Bucket.return_value.wait_until_exists
        self.charm.backup._create_bucket_if_not_exists()
        head_bucket.assert_called_once()
        create.assert_not_called()
        wait_until_exists.assert_not_called()

        # Test when the bucket doesn't exist.
        head_bucket.reset_mock()
        head_bucket.side_effect = ClientError(
            error_response={"Error": {"Code": 1, "message": "fake error"}},
            operation_name="fake operation name",
        )
        self.charm.backup._create_bucket_if_not_exists()
        head_bucket.assert_called_once()
        create.assert_called_once()
        wait_until_exists.assert_called_once()

        # Test when the bucket creation fails.
        head_bucket.reset_mock()
        create.reset_mock()
        wait_until_exists.reset_mock()
        create.side_effect = ClientError(
            error_response={"Error": {"Code": 1, "message": "fake error"}},
            operation_name="fake operation name",
        )
        with self.assertRaises(ClientError):
            self.charm.backup._create_bucket_if_not_exists()
        head_bucket.assert_called_once()
        create.assert_called_once()
        wait_until_exists.assert_not_called()

    @patch("ops.model.Container.exec")
    def test_empty_data_files(self, _exec):
        # Test when the removal of the data files fails.
        command = "rm -r /var/lib/postgresql/data/pgdata".split()
        _exec.side_effect = ExecError(command=command, exit_code=1, stdout="", stderr="fake error")
        with self.assertRaises(ExecError):
            self.charm.backup._empty_data_files()
        _exec.assert_called_once_with(command)

        # Test when data files are successfully removed.
        _exec.reset_mock()
        _exec.side_effect = None
        self.charm.backup._empty_data_files()
        _exec.assert_called_once_with(command)

    @patch("charm.PostgresqlOperatorCharm.update_config")
    def test_change_connectivity_to_database(self, _update_config):
        # Ensure that there is no connectivity info in the unit relation databag.
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.peer_rel_id,
                self.charm.unit.name,
                {"connectivity": ""},
            )

        # Test when connectivity should be turned on.
        self.charm.backup._change_connectivity_to_database(True)
        self.assertEqual(
            self.harness.get_relation_data(self.peer_rel_id, self.charm.unit),
            {"connectivity": "on"},
        )
        _update_config.assert_called_once()

        # Test when connectivity should be turned off.
        _update_config.reset_mock()
        self.charm.backup._change_connectivity_to_database(False)
        self.assertEqual(
            self.harness.get_relation_data(self.peer_rel_id, self.charm.unit),
            {"connectivity": "off"},
        )
        _update_config.assert_called_once()

    @patch("ops.model.Container.exec")
    def test_execute_command(self, _exec):
        # Test when the command fails.
        command = "rm -r /var/lib/postgresql/data/pgdata".split()
        _exec.side_effect = ChangeError(
            err="fake error",
            change=Change(
                ChangeID("1"),
                "fake kind",
                "fake summary",
                "fake status",
                [],
                True,
                "fake error",
                datetime.datetime.now(),
                datetime.datetime.now(),
            ),
        )
        _exec.return_value.wait_output.return_value = ("fake stdout", "")
        self.assertEqual(self.charm.backup._execute_command(command), (None, None))
        _exec.assert_called_once_with(command, user="postgres", group="postgres", timeout=None)

        # Test when the command runs successfully.
        _exec.reset_mock()
        _exec.side_effect = None
        self.assertEqual(
            self.charm.backup._execute_command(command, timeout=5), ("fake stdout", "")
        )
        _exec.assert_called_once_with(command, user="postgres", group="postgres", timeout=5)

    def test_format_backup_list(self):
        # Test when there are no backups.
        self.assertEqual(
            self.charm.backup._format_backup_list([]),
            """backup-id             | backup-type  | backup-status
----------------------------------------------------""",
        )

        # Test when there are backups.
        backup_list = [
            ("2023-01-01T09:00:00Z", "physical", "failed: fake error"),
            ("2023-01-01T10:00:00Z", "physical", "finished"),
        ]
        self.assertEqual(
            self.charm.backup._format_backup_list(backup_list),
            """backup-id             | backup-type  | backup-status
----------------------------------------------------
2023-01-01T09:00:00Z  | physical     | failed: fake error
2023-01-01T10:00:00Z  | physical     | finished""",
        )

    @patch("charm.PostgreSQLBackups._execute_command")
    def test_generate_backup_list_output(self, _execute_command):
        # Test when no backups are returned.
        _execute_command.return_value = ('[{"backup":[]}]', None)
        self.assertEqual(
            self.charm.backup._generate_backup_list_output(),
            """backup-id             | backup-type  | backup-status
----------------------------------------------------""",
        )

        # Test when backups are returned.
        _execute_command.return_value = (
            '[{"backup":[{"label":"20230101-090000F","error":"fake error"},{"label":"20230101-100000F","error":null}]}]',
            None,
        )
        self.assertEqual(
            self.charm.backup._generate_backup_list_output(),
            """backup-id             | backup-type  | backup-status
----------------------------------------------------
2023-01-01T09:00:00Z  | physical     | failed: fake error
2023-01-01T10:00:00Z  | physical     | finished""",
        )

    @patch("charm.PostgreSQLBackups._execute_command")
    def test_list_backups(self, _execute_command):
        # Test when no backups are available.
        _execute_command.return_value = ("[]", None)
        self.assertEqual(
            self.charm.backup._list_backups(show_failed=True), OrderedDict[str, str]()
        )

        # Test when some backups are available.
        _execute_command.return_value = (
            '[{"backup":[{"label":"20230101-090000F","error":"fake error"},{"label":"20230101-100000F","error":null}],"name":"test-stanza"}]',
            None,
        )
        self.assertEqual(
            self.charm.backup._list_backups(show_failed=True),
            OrderedDict[str, str]([
                ("2023-01-01T09:00:00Z", "test-stanza"),
                ("2023-01-01T10:00:00Z", "test-stanza"),
            ]),
        )

        # Test when some backups are available, but it's not desired to list failed backups.
        self.assertEqual(
            self.charm.backup._list_backups(show_failed=False),
            OrderedDict[str, str]([("2023-01-01T10:00:00Z", "test-stanza")]),
        )

    @patch("charm.Patroni.reload_patroni_configuration")
    @patch("charm.Patroni.member_started", new_callable=PropertyMock)
    @patch("backups.wait_fixed", return_value=wait_fixed(0))
    @patch("charm.PostgresqlOperatorCharm.update_config")
    @patch("charm.PostgreSQLBackups._execute_command")
    @patch("charm.PostgresqlOperatorCharm.is_primary", new_callable=PropertyMock)
    def test_initialise_stanza(
        self,
        _is_primary,
        _execute_command,
        _update_config,
        _,
        _member_started,
        _reload_patroni_configuration,
    ):
        # Test when the unit is not the primary.
        _is_primary.return_value = False
        self.charm.backup._initialise_stanza()
        _execute_command.assert_not_called()

        # Test when the unit is the primary, but it's in a blocked state
        # other than the ones can be solved by new S3 settings.
        _is_primary.return_value = True
        self.charm.unit.status = BlockedStatus("fake blocked state")
        self.charm.backup._initialise_stanza()
        _execute_command.assert_not_called()

        # Test when the blocked state is any of the blocked stated that can be solved
        # by new S3 settings, but the stanza creation fails.
        stanza_creation_command = [
            "pgbackrest",
            f"--stanza={self.charm.backup.stanza_name}",
            "stanza-create",
        ]
        _execute_command.side_effect = ExecError(
            command=stanza_creation_command, exit_code=1, stdout="", stderr="fake error"
        )
        for blocked_state in [
            ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE,
            FAILED_TO_ACCESS_CREATE_BUCKET_ERROR_MESSAGE,
            FAILED_TO_INITIALIZE_STANZA_ERROR_MESSAGE,
        ]:
            _execute_command.reset_mock()
            self.charm.unit.status = BlockedStatus(blocked_state)
            self.charm.backup._initialise_stanza()
            _execute_command.assert_called_once_with(stanza_creation_command)
            self.assertIsInstance(self.charm.unit.status, BlockedStatus)
            self.assertEqual(
                self.charm.unit.status.message, FAILED_TO_INITIALIZE_STANZA_ERROR_MESSAGE
            )

            # Assert there is no stanza name in the application relation databag.
            self.assertEqual(self.harness.get_relation_data(self.peer_rel_id, self.charm.app), {})

        # Test when the archiving is working correctly (pgBackRest check command succeeds)
        # and the unit is not the leader.
        _execute_command.reset_mock()
        _update_config.reset_mock()
        _member_started.reset_mock()
        _reload_patroni_configuration.reset_mock()
        _execute_command.side_effect = None
        self.charm.backup._initialise_stanza()
        self.assertEqual(self.harness.get_relation_data(self.peer_rel_id, self.charm.app), {})
        self.assertEqual(
            self.harness.get_relation_data(self.peer_rel_id, self.charm.unit),
            {
                "stanza": f"{self.charm.model.name}.patroni-postgresql-k8s",
                "init-pgbackrest": "True",
            },
        )
        self.assertIsInstance(self.charm.unit.status, MaintenanceStatus)

        # Test when the unit is the leader.
        with self.harness.hooks_disabled():
            self.harness.set_leader()
            self.harness.update_relation_data(
                self.peer_rel_id, self.charm.unit.name, {"stanza": "", "init-pgbackrest": ""}
            )
        self.charm.backup._initialise_stanza()
        self.assertEqual(
            self.harness.get_relation_data(self.peer_rel_id, self.charm.app),
            {
                "stanza": f"{self.charm.model.name}.patroni-postgresql-k8s",
                "init-pgbackrest": "True",
            },
        )
        self.assertEqual(
            self.harness.get_relation_data(self.peer_rel_id, self.charm.unit),
            {},
        )
        self.assertIsInstance(self.charm.unit.status, MaintenanceStatus)

    @patch("charm.Patroni.reload_patroni_configuration")
    @patch("charm.Patroni.member_started", new_callable=PropertyMock)
    @patch("backups.wait_fixed", return_value=wait_fixed(0))
    @patch("charm.PostgresqlOperatorCharm.update_config")
    @patch("charm.PostgreSQLBackups._execute_command")
    @patch("charm.PostgresqlOperatorCharm.is_primary", new_callable=PropertyMock)
    def test_check_stanza(
        self,
        _is_primary,
        _execute_command,
        _update_config,
        _,
        _member_started,
        _reload_patroni_configuration,
    ):
        # Set peer data flag
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.peer_rel_id,
                self.charm.app.name,
                {"stanza": "test-stanza", "init-pgbackrest": "True"},
            )
            self.harness.update_relation_data(
                self.peer_rel_id,
                self.charm.unit.name,
                {"stanza": "test-stanza", "init-pgbackrest": "True"},
            )

        # Test when the unit is not the primary.
        _is_primary.return_value = False
        self.charm.backup.check_stanza()
        _execute_command.assert_not_called()

        # Set the unit as primary.
        _is_primary.return_value = True

        stanza_check_command = [
            "pgbackrest",
            f"--stanza={self.charm.backup.stanza_name}",
            "check",
        ]
        # Test when the archiving is not working correctly (pgBackRest check command fails).
        _execute_command.side_effect = ExecError(
            command=stanza_check_command, exit_code=1, stdout="", stderr="fake error"
        )
        _member_started.return_value = True
        self.charm.backup.check_stanza()
        self.assertEqual(_update_config.call_count, 2)
        self.assertEqual(_member_started.call_count, 5)
        self.assertEqual(_reload_patroni_configuration.call_count, 5)
        self.assertEqual(self.harness.get_relation_data(self.peer_rel_id, self.charm.app), {})
        self.assertEqual(self.harness.get_relation_data(self.peer_rel_id, self.charm.unit), {})
        self.assertIsInstance(self.charm.unit.status, BlockedStatus)
        self.assertEqual(self.charm.unit.status.message, FAILED_TO_INITIALIZE_STANZA_ERROR_MESSAGE)

        # Test when the archiving is working correctly (pgBackRest check command succeeds)
        # and the unit is not the leader.
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.peer_rel_id,
                self.charm.app.name,
                {"stanza": "test-stanza", "init-pgbackrest": "True"},
            )
            self.harness.update_relation_data(
                self.peer_rel_id,
                self.charm.unit.name,
                {"stanza": "test-stanza", "init-pgbackrest": "True"},
            )
        _execute_command.reset_mock()
        _update_config.reset_mock()
        _member_started.reset_mock()
        _reload_patroni_configuration.reset_mock()
        _execute_command.side_effect = None
        self.charm.backup.check_stanza()
        _update_config.assert_called_once()
        _member_started.assert_called_once()
        _reload_patroni_configuration.assert_called_once()
        self.assertEqual(
            self.harness.get_relation_data(self.peer_rel_id, self.charm.app),
            {"stanza": "test-stanza", "init-pgbackrest": "True"},
        )
        self.assertEqual(
            self.harness.get_relation_data(self.peer_rel_id, self.charm.unit),
            {"stanza": "test-stanza"},
        )
        self.assertIsInstance(self.charm.unit.status, ActiveStatus)

        # Test when the unit is the leader.
        self.charm.unit.status = BlockedStatus("fake blocked state")
        with self.harness.hooks_disabled():
            self.harness.set_leader()
            self.harness.update_relation_data(
                self.peer_rel_id,
                self.charm.app.name,
                {"init-pgbackrest": "True"},
            )
            self.harness.update_relation_data(
                self.peer_rel_id,
                self.charm.unit.name,
                {"init-pgbackrest": "True"},
            )
        _update_config.reset_mock()
        _member_started.reset_mock()
        _reload_patroni_configuration.reset_mock()
        self.charm.backup.check_stanza()
        _update_config.assert_called_once()
        _member_started.assert_called_once()
        _reload_patroni_configuration.assert_called_once()
        self.assertEqual(
            self.harness.get_relation_data(self.peer_rel_id, self.charm.app),
            {"stanza": "test-stanza"},
        )
        self.assertEqual(
            self.harness.get_relation_data(self.peer_rel_id, self.charm.unit),
            {"stanza": "test-stanza"},
        )
        self.assertIsInstance(self.charm.unit.status, ActiveStatus)

    def test_coordinate_stanza_fields(self):
        # Add a new unit to the relation.
        new_unit_name = "postgresql-k8s/1"
        new_unit = Unit(new_unit_name, None, self.harness.charm.app._backend, {})
        self.harness.add_relation_unit(self.peer_rel_id, new_unit_name)

        # Test when the stanza name is neither in the application relation databag nor in the unit relation databag.
        self.charm.backup.coordinate_stanza_fields()
        self.assertEqual(self.harness.get_relation_data(self.peer_rel_id, self.charm.app), {})
        self.assertEqual(self.harness.get_relation_data(self.peer_rel_id, self.charm.unit), {})
        self.assertEqual(self.harness.get_relation_data(self.peer_rel_id, new_unit), {})

        # Test when the stanza name is in the unit relation databag but the unit is not the leader.
        stanza_name = f"{self.charm.model.name}.patroni-{self.charm.app.name}"
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.peer_rel_id, new_unit_name, {"stanza": stanza_name, "init-pgbackrest": "True"}
            )
        self.charm.backup.coordinate_stanza_fields()
        self.assertEqual(self.harness.get_relation_data(self.peer_rel_id, self.charm.app), {})
        self.assertEqual(self.harness.get_relation_data(self.peer_rel_id, self.charm.unit), {})
        self.assertEqual(
            self.harness.get_relation_data(self.peer_rel_id, new_unit),
            {"stanza": stanza_name, "init-pgbackrest": "True"},
        )

        # Test when the unit is the leader.
        with self.harness.hooks_disabled():
            self.harness.set_leader()
        self.charm.backup.coordinate_stanza_fields()
        self.assertEqual(
            self.harness.get_relation_data(self.peer_rel_id, self.charm.app),
            {"stanza": stanza_name, "init-pgbackrest": "True"},
        )
        self.assertEqual(self.harness.get_relation_data(self.peer_rel_id, self.charm.unit), {})
        self.assertEqual(
            self.harness.get_relation_data(self.peer_rel_id, new_unit),
            {"stanza": stanza_name, "init-pgbackrest": "True"},
        )

        # Test when the stanza was already checked in the primary non-leader unit.
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.peer_rel_id, new_unit_name, {"init-pgbackrest": ""}
            )
        self.charm.backup.coordinate_stanza_fields()
        self.assertEqual(
            self.harness.get_relation_data(self.peer_rel_id, self.charm.app),
            {"stanza": stanza_name},
        )
        self.assertEqual(self.harness.get_relation_data(self.peer_rel_id, self.charm.unit), {})
        self.assertEqual(
            self.harness.get_relation_data(self.peer_rel_id, new_unit), {"stanza": stanza_name}
        )

        # Test when the "init-pgbackrest" flag was removed from the application relation databag
        # and this is the unit that has the stanza name in the unit relation databag.
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.peer_rel_id, self.charm.unit.name, {"stanza": stanza_name}
            )
        self.charm.backup.coordinate_stanza_fields()
        self.assertEqual(
            self.harness.get_relation_data(self.peer_rel_id, self.charm.app),
            {"stanza": stanza_name},
        )
        self.assertEqual(self.harness.get_relation_data(self.peer_rel_id, self.charm.unit), {})
        self.assertEqual(
            self.harness.get_relation_data(self.peer_rel_id, new_unit), {"stanza": stanza_name}
        )

        # Test when the unit is not the leader.
        with self.harness.hooks_disabled():
            self.harness.set_leader(False)
            self.harness.update_relation_data(
                self.peer_rel_id, self.charm.unit.name, {"stanza": stanza_name}
            )
        self.charm.backup.coordinate_stanza_fields()
        self.assertEqual(
            self.harness.get_relation_data(self.peer_rel_id, self.charm.app),
            {"stanza": stanza_name},
        )
        self.assertEqual(self.harness.get_relation_data(self.peer_rel_id, self.charm.unit), {})
        self.assertEqual(
            self.harness.get_relation_data(self.peer_rel_id, new_unit), {"stanza": stanza_name}
        )

    @patch("charm.PostgreSQLBackups._execute_command")
    @patch("charm.PostgresqlOperatorCharm._get_hostname_from_unit")
    @patch("charm.Patroni.get_primary")
    def test_is_primary_pgbackrest_service_running(
        self, _get_primary, _get_hostname_from_unit, _execute_command
    ):
        # Test when the charm fails to get the current primary.
        _get_primary.side_effect = RetryError(last_attempt=1)
        self.assertEqual(self.charm.backup._is_primary_pgbackrest_service_running, False)
        _execute_command.assert_not_called()

        # Test when the primary was not elected yet.
        _get_primary.side_effect = None
        _get_primary.return_value = None
        self.assertEqual(self.charm.backup._is_primary_pgbackrest_service_running, False)
        _execute_command.assert_not_called()

        # Test when the pgBackRest fails to contact the primary server.
        _get_primary.return_value = f"{self.charm.app.name}/1"
        _execute_command.side_effect = ExecError(
            command="fake command".split(), exit_code=1, stdout="", stderr="fake error"
        )
        self.assertEqual(self.charm.backup._is_primary_pgbackrest_service_running, False)
        _execute_command.assert_called_once()

        # Test when the pgBackRest succeeds on contacting the primary server.
        _execute_command.reset_mock()
        _execute_command.side_effect = None
        self.assertEqual(self.charm.backup._is_primary_pgbackrest_service_running, True)
        _execute_command.assert_called_once()

    @patch("charm.PostgreSQLBackups._initialise_stanza")
    @patch("charm.PostgreSQLBackups.can_use_s3_repository")
    @patch("charm.PostgreSQLBackups._create_bucket_if_not_exists")
    @patch("charm.PostgresqlOperatorCharm.is_primary", new_callable=PropertyMock)
    @patch("charm.PostgreSQLBackups._render_pgbackrest_conf_file")
    @patch("ops.framework.EventBase.defer")
    def test_on_s3_credential_changed(
        self,
        _defer,
        _render_pgbackrest_conf_file,
        _is_primary,
        _create_bucket_if_not_exists,
        _can_use_s3_repository,
        _initialise_stanza,
    ):
        # Test when the cluster was not initialised yet.
        self.relate_to_s3_integrator()
        self.charm.backup.s3_client.on.credentials_changed.emit(
            relation=self.harness.model.get_relation(S3_PARAMETERS_RELATION, self.s3_rel_id)
        )
        _defer.assert_called_once()
        _render_pgbackrest_conf_file.assert_not_called()
        _create_bucket_if_not_exists.assert_not_called()
        _can_use_s3_repository.assert_not_called()
        _initialise_stanza.assert_not_called()

        # Test when the cluster is already initialised, but the charm fails to render
        # the pgBackRest configuration file due to missing S3 parameters.
        _defer.reset_mock()
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.peer_rel_id,
                self.charm.app.name,
                {"cluster_initialised": "True"},
            )
        _render_pgbackrest_conf_file.return_value = False
        self.charm.backup.s3_client.on.credentials_changed.emit(
            relation=self.harness.model.get_relation(S3_PARAMETERS_RELATION, self.s3_rel_id)
        )
        _defer.assert_not_called()
        _render_pgbackrest_conf_file.assert_called_once()
        _create_bucket_if_not_exists.assert_not_called()
        _can_use_s3_repository.assert_not_called()
        _initialise_stanza.assert_not_called()

        # Test that followers will not initialise the bucket
        self.charm.unit.status = ActiveStatus()
        _render_pgbackrest_conf_file.reset_mock()
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.peer_rel_id,
                self.charm.app.name,
                {"cluster_initialised": "True"},
            )
        _render_pgbackrest_conf_file.return_value = True
        _is_primary.return_value = False

        self.charm.backup.s3_client.on.credentials_changed.emit(
            relation=self.harness.model.get_relation(S3_PARAMETERS_RELATION, self.s3_rel_id)
        )
        _render_pgbackrest_conf_file.assert_called_once()
        _create_bucket_if_not_exists.assert_not_called()
        self.assertIsInstance(self.charm.unit.status, ActiveStatus)
        _can_use_s3_repository.assert_not_called()
        _initialise_stanza.assert_not_called()

        # Test when the charm render the pgBackRest configuration file, but fails to
        # access or create the S3 bucket.
        _is_primary.return_value = True
        for error in [
            ClientError(
                error_response={"Error": {"Code": 1, "message": "fake error"}},
                operation_name="fake operation name",
            ),
            ValueError,
        ]:
            _render_pgbackrest_conf_file.reset_mock()
            _create_bucket_if_not_exists.reset_mock()
            _create_bucket_if_not_exists.side_effect = error
            self.charm.backup.s3_client.on.credentials_changed.emit(
                relation=self.harness.model.get_relation(S3_PARAMETERS_RELATION, self.s3_rel_id)
            )
            _render_pgbackrest_conf_file.assert_called_once()
            _create_bucket_if_not_exists.assert_called_once()
            self.assertIsInstance(self.charm.unit.status, BlockedStatus)
            self.assertEqual(
                self.charm.unit.status.message, FAILED_TO_ACCESS_CREATE_BUCKET_ERROR_MESSAGE
            )
            _can_use_s3_repository.assert_not_called()
            _initialise_stanza.assert_not_called()

        # Test when it's not possible to use the S3 repository due to backups from another cluster.
        _create_bucket_if_not_exists.reset_mock()
        _create_bucket_if_not_exists.side_effect = None
        _can_use_s3_repository.return_value = (False, "fake validation message")
        self.charm.backup.s3_client.on.credentials_changed.emit(
            relation=self.harness.model.get_relation(S3_PARAMETERS_RELATION, self.s3_rel_id)
        )
        self.assertIsInstance(self.charm.unit.status, BlockedStatus)
        self.assertEqual(self.charm.unit.status.message, "fake validation message")
        _create_bucket_if_not_exists.assert_called_once()
        _can_use_s3_repository.assert_called_once()
        _initialise_stanza.assert_not_called()

        # Test when the stanza can be initialised and the pgBackRest service can start.
        _can_use_s3_repository.reset_mock()
        _can_use_s3_repository.return_value = (True, None)
        self.charm.backup.s3_client.on.credentials_changed.emit(
            relation=self.harness.model.get_relation(S3_PARAMETERS_RELATION, self.s3_rel_id)
        )
        _can_use_s3_repository.assert_called_once()
        _initialise_stanza.assert_called_once()

    def test_on_s3_credential_gone(self):
        # Test that unrelated blocks will remain
        self.charm.unit.status = BlockedStatus("test block")
        self.charm.backup._on_s3_credential_gone(None)
        self.assertIsInstance(self.charm.unit.status, BlockedStatus)

        # Test that s3 related blocks will be cleared
        self.charm.unit.status = BlockedStatus(ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE)
        self.charm.backup._on_s3_credential_gone(None)
        self.assertIsInstance(self.charm.unit.status, ActiveStatus)

        # Test removal of relation data when the unit is not the leader.
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.peer_rel_id,
                self.charm.app.name,
                {"stanza": "test-stanza", "init-pgbackrest": "True"},
            )
            self.harness.update_relation_data(
                self.peer_rel_id,
                self.charm.app.name,
                {"stanza": "test-stanza", "init-pgbackrest": "True"},
            )
        self.charm.backup._on_s3_credential_gone(None)
        self.assertEqual(
            self.harness.get_relation_data(self.peer_rel_id, self.charm.app),
            {"stanza": "test-stanza", "init-pgbackrest": "True"},
        )
        self.assertEqual(self.harness.get_relation_data(self.peer_rel_id, self.charm.unit), {})

        # Test removal of relation data when the unit is the leader.
        with self.harness.hooks_disabled():
            self.harness.set_leader()
            self.harness.update_relation_data(
                self.peer_rel_id,
                self.charm.unit.name,
                {"stanza": "test-stanza", "init-pgbackrest": "True"},
            )
        self.charm.backup._on_s3_credential_gone(None)
        self.assertEqual(self.harness.get_relation_data(self.peer_rel_id, self.charm.app), {})
        self.assertEqual(self.harness.get_relation_data(self.peer_rel_id, self.charm.unit), {})

    @patch("charm.PostgresqlOperatorCharm.update_config")
    @patch("charm.PostgreSQLBackups._change_connectivity_to_database")
    @patch("charm.PostgreSQLBackups._list_backups")
    @patch("charm.PostgreSQLBackups._execute_command")
    @patch("charm.PostgresqlOperatorCharm.is_primary", new_callable=PropertyMock)
    @patch("charm.PostgreSQLBackups._upload_content_to_s3")
    @patch("backups.datetime")
    @patch("ops.JujuVersion.from_environ")
    @patch("charm.PostgreSQLBackups._retrieve_s3_parameters")
    @patch("charm.PostgreSQLBackups._can_unit_perform_backup")
    def test_on_create_backup_action(
        self,
        _can_unit_perform_backup,
        _retrieve_s3_parameters,
        _from_environ,
        _datetime,
        _upload_content_to_s3,
        _is_primary,
        _execute_command,
        _list_backups,
        _change_connectivity_to_database,
        _update_config,
    ):
        # Test when the unit cannot perform a backup.
        mock_event = MagicMock()
        _can_unit_perform_backup.return_value = (False, "fake validation message")
        self.charm.backup._on_create_backup_action(mock_event)
        mock_event.fail.assert_called_once()
        mock_event.set_results.assert_not_called()

        # Test when the charm fails to upload a file to S3.
        mock_event.reset_mock()
        _can_unit_perform_backup.return_value = (True, None)
        mock_s3_parameters = {
            "bucket": "test-bucket",
            "access-key": "test-access-key",
            "secret-key": "test-secret-key",
            "endpoint": "test-endpoint",
            "path": "test-path",
            "region": "test-region",
        }
        _retrieve_s3_parameters.return_value = (
            mock_s3_parameters,
            [],
        )
        _datetime.now.return_value.strftime.return_value = "2023-01-01T09:00:00Z"
        _from_environ.return_value = "test-juju-version"
        _upload_content_to_s3.return_value = False
        expected_metadata = f"""Date Backup Requested: 2023-01-01T09:00:00Z
Model Name: {self.charm.model.name}
Application Name: {self.charm.model.app.name}
Unit Name: {self.charm.unit.name}
Juju Version: test-juju-version
"""
        self.charm.backup._on_create_backup_action(mock_event)
        _upload_content_to_s3.assert_called_once_with(
            expected_metadata,
            f"test-path/backup/{self.charm.model.name}.{self.charm.cluster_name}/latest",
            mock_s3_parameters,
        )
        mock_event.fail.assert_called_once()
        mock_event.set_results.assert_not_called()

        # Test when the backup fails.
        mock_event.reset_mock()
        _upload_content_to_s3.return_value = True
        _is_primary.return_value = True
        _execute_command.side_effect = ExecError(
            command="fake command".split(), exit_code=1, stdout="", stderr="fake error"
        )
        self.charm.backup._on_create_backup_action(mock_event)
        update_config_calls = [
            call(is_creating_backup=True),
            call(is_creating_backup=False),
        ]
        _update_config.assert_has_calls(update_config_calls)
        mock_event.fail.assert_called_once()
        mock_event.set_results.assert_not_called()

        # Test when the backup succeeds but the charm fails to upload the backup logs.
        mock_event.reset_mock()
        _upload_content_to_s3.reset_mock()
        _upload_content_to_s3.side_effect = [True, False]
        _execute_command.side_effect = None
        _execute_command.return_value = "fake stdout", "fake stderr"
        _list_backups.return_value = {"2023-01-01T09:00:00Z": self.charm.backup.stanza_name}
        _update_config.reset_mock()
        self.charm.backup._on_create_backup_action(mock_event)
        _upload_content_to_s3.assert_has_calls([
            call(
                expected_metadata,
                f"test-path/backup/{self.charm.model.name}.{self.charm.cluster_name}/latest",
                mock_s3_parameters,
            ),
            call(
                "Stdout:\nfake stdout\n\nStderr:\nfake stderr\n",
                f"test-path/backup/{self.charm.model.name}.{self.charm.cluster_name}/2023-01-01T09:00:00Z/backup.log",
                mock_s3_parameters,
            ),
        ])
        _update_config.assert_has_calls(update_config_calls)
        mock_event.fail.assert_called_once()
        mock_event.set_results.assert_not_called()

        # Test when the backup succeeds (including the upload of the backup logs).
        mock_event.reset_mock()
        _upload_content_to_s3.reset_mock()
        _upload_content_to_s3.side_effect = None
        _upload_content_to_s3.return_value = True
        _update_config.reset_mock()
        self.charm.backup._on_create_backup_action(mock_event)
        _upload_content_to_s3.assert_has_calls([
            call(
                expected_metadata,
                f"test-path/backup/{self.charm.model.name}.{self.charm.cluster_name}/latest",
                mock_s3_parameters,
            ),
            call(
                "Stdout:\nfake stdout\n\nStderr:\nfake stderr\n",
                f"test-path/backup/{self.charm.model.name}.{self.charm.cluster_name}/2023-01-01T09:00:00Z/backup.log",
                mock_s3_parameters,
            ),
        ])
        _change_connectivity_to_database.assert_not_called()
        _update_config.assert_has_calls(update_config_calls)
        mock_event.fail.assert_not_called()
        mock_event.set_results.assert_called_once()

        # Test when this unit is a replica (the connectivity to the database should be changed).
        mock_event.reset_mock()
        _upload_content_to_s3.reset_mock()
        _is_primary.return_value = False
        self.charm.backup._on_create_backup_action(mock_event)
        _upload_content_to_s3.assert_has_calls([
            call(
                expected_metadata,
                f"test-path/backup/{self.charm.model.name}.{self.charm.cluster_name}/latest",
                mock_s3_parameters,
            ),
            call(
                "Stdout:\nfake stdout\n\nStderr:\nfake stderr\n",
                f"test-path/backup/{self.charm.model.name}.{self.charm.cluster_name}/2023-01-01T09:00:00Z/backup.log",
                mock_s3_parameters,
            ),
        ])
        self.assertEqual(_change_connectivity_to_database.call_count, 2)
        mock_event.fail.assert_not_called()
        mock_event.set_results.assert_called_once_with({"backup-status": "backup created"})

    @patch("charm.PostgreSQLBackups._generate_backup_list_output")
    @patch("charm.PostgreSQLBackups._are_backup_settings_ok")
    def test_on_list_backups_action(self, _are_backup_settings_ok, _generate_backup_list_output):
        # Test when not all backup settings are ok.
        mock_event = MagicMock()
        _are_backup_settings_ok.return_value = (False, "fake validation message")
        self.charm.backup._on_list_backups_action(mock_event)
        mock_event.fail.assert_called_once()
        _generate_backup_list_output.assert_not_called()
        mock_event.set_results.assert_not_called()

        # Test when the charm fails to generate the backup list output.
        mock_event.reset_mock()
        _are_backup_settings_ok.return_value = (True, None)
        _generate_backup_list_output.side_effect = ExecError(
            command="fake command".split(), exit_code=1, stdout="", stderr="fake error"
        )
        self.charm.backup._on_list_backups_action(mock_event)
        _generate_backup_list_output.assert_called_once()
        mock_event.fail.assert_called_once()
        mock_event.set_results.assert_not_called()

        # Test when the charm succeeds on generating the backup list output.
        mock_event.reset_mock()
        _generate_backup_list_output.reset_mock()
        _are_backup_settings_ok.return_value = (True, None)
        _generate_backup_list_output.side_effect = None
        _generate_backup_list_output.return_value = """backup-id             | backup-type  | backup-status
----------------------------------------------------
2023-01-01T09:00:00Z  | physical     | failed: fake error
2023-01-01T10:00:00Z  | physical     | finished"""
        self.charm.backup._on_list_backups_action(mock_event)
        _generate_backup_list_output.assert_called_once()
        mock_event.set_results.assert_called_once_with({
            "backups": """backup-id             | backup-type  | backup-status
----------------------------------------------------
2023-01-01T09:00:00Z  | physical     | failed: fake error
2023-01-01T10:00:00Z  | physical     | finished"""
        })
        mock_event.fail.assert_not_called()

    @patch("ops.model.Container.start")
    @patch("charm.PostgresqlOperatorCharm.update_config")
    @patch("charm.PostgresqlOperatorCharm._create_pgdata")
    @patch("charm.PostgreSQLBackups._empty_data_files")
    @patch("charm.PostgreSQLBackups._restart_database")
    @patch("lightkube.Client.delete")
    @patch("ops.model.Container.stop")
    @patch("charm.PostgreSQLBackups._list_backups")
    @patch("charm.PostgreSQLBackups._pre_restore_checks")
    def test_on_restore_action(
        self,
        _pre_restore_checks,
        _list_backups,
        _stop,
        _delete,
        _restart_database,
        _empty_data_files,
        _create_pgdata,
        _update_config,
        _start,
    ):
        # Test when pre restore checks fail.
        mock_event = MagicMock()
        _pre_restore_checks.return_value = False
        self.charm.unit.status = ActiveStatus()
        self.charm.backup._on_restore_action(mock_event)
        _list_backups.assert_not_called()
        _stop.assert_not_called()
        _delete.assert_not_called()
        _restart_database.assert_not_called()
        _empty_data_files.assert_not_called()
        _create_pgdata.assert_not_called()
        _update_config.assert_not_called()
        _start.assert_not_called()
        mock_event.fail.assert_not_called()
        mock_event.set_results.assert_not_called()
        self.assertNotIsInstance(self.charm.unit.status, MaintenanceStatus)

        # Test when the user provides an invalid backup id.
        mock_event.params = {"backup-id": "2023-01-01T10:00:00Z"}
        _pre_restore_checks.return_value = True
        _list_backups.return_value = {"2023-01-01T09:00:00Z": self.charm.backup.stanza_name}
        self.charm.unit.status = ActiveStatus()
        self.charm.backup._on_restore_action(mock_event)
        _list_backups.assert_called_once_with(show_failed=False)
        mock_event.fail.assert_called_once()
        _stop.assert_not_called()
        _delete.assert_not_called()
        _restart_database.assert_not_called()
        _empty_data_files.assert_not_called()
        _create_pgdata.assert_not_called()
        _update_config.assert_not_called()
        _start.assert_not_called()
        mock_event.set_results.assert_not_called()
        self.assertNotIsInstance(self.charm.unit.status, MaintenanceStatus)

        # Test when the charm fails to stop the workload.
        mock_event.reset_mock()
        mock_event.params = {"backup-id": "2023-01-01T09:00:00Z"}
        _stop.side_effect = ChangeError(
            err="fake error",
            change=Change(
                ChangeID("1"),
                "fake kind",
                "fake summary",
                "fake status",
                [],
                True,
                "fake error",
                datetime.datetime.now(),
                datetime.datetime.now(),
            ),
        )
        self.charm.backup._on_restore_action(mock_event)
        _stop.assert_called_once_with("postgresql")
        mock_event.fail.assert_called_once()
        _delete.assert_not_called()
        _restart_database.assert_not_called()
        _empty_data_files.assert_not_called()
        _create_pgdata.assert_not_called()
        _update_config.assert_not_called()
        _start.assert_not_called()
        mock_event.set_results.assert_not_called()

        # Test when the charm fails to remove the previous cluster information.
        mock_event.reset_mock()
        mock_event.params = {"backup-id": "2023-01-01T09:00:00Z"}
        _stop.side_effect = None
        _delete.side_effect = [None, _FakeApiError]
        self.charm.backup._on_restore_action(mock_event)
        self.assertEqual(_delete.call_count, 2)
        mock_event.fail.assert_called_once()
        _restart_database.assert_called_once()
        _empty_data_files.assert_not_called()
        _create_pgdata.assert_not_called()
        _update_config.assert_not_called()
        _start.assert_not_called()
        mock_event.set_results.assert_not_called()

        # Test when the charm fails to remove the files from the data directory.
        mock_event.reset_mock()
        _restart_database.reset_mock()
        _delete.side_effect = None
        _empty_data_files.side_effect = ExecError(
            command="fake command".split(), exit_code=1, stdout="", stderr="fake error"
        )
        self.charm.backup._on_restore_action(mock_event)
        _empty_data_files.assert_called_once()
        mock_event.fail.assert_called_once()
        _restart_database.assert_called_once()
        _create_pgdata.assert_not_called()
        _update_config.assert_not_called()
        _start.assert_not_called()
        mock_event.set_results.assert_not_called()

        # Test a successful start of the restore process.
        mock_event.reset_mock()
        _restart_database.reset_mock()
        _empty_data_files.side_effect = None
        self.assertEqual(self.harness.get_relation_data(self.peer_rel_id, self.charm.app), {})
        self.charm.backup._on_restore_action(mock_event)
        _restart_database.assert_not_called()
        self.assertEqual(
            self.harness.get_relation_data(self.peer_rel_id, self.charm.app),
            {
                "restoring-backup": "20230101-090000F",
                "restore-stanza": f"{self.charm.model.name}.{self.charm.cluster_name}",
            },
        )
        _create_pgdata.assert_called_once()
        _update_config.assert_called_once()
        _start.assert_called_once_with("postgresql")
        mock_event.fail.assert_not_called()
        mock_event.set_results.assert_called_once_with({"restore-status": "restore started"})

    @patch("ops.model.Application.planned_units")
    @patch("charm.PostgreSQLBackups._are_backup_settings_ok")
    def test_pre_restore_checks(self, _are_backup_settings_ok, _planned_units):
        # Test when S3 parameters are not ok.
        mock_event = MagicMock()
        _are_backup_settings_ok.return_value = (False, "fake error message")
        self.assertEqual(self.charm.backup._pre_restore_checks(mock_event), False)
        mock_event.fail.assert_called_once()

        # Test when no backup id is provided.
        mock_event.reset_mock()
        _are_backup_settings_ok.return_value = (True, None)
        self.assertEqual(self.charm.backup._pre_restore_checks(mock_event), False)
        mock_event.fail.assert_called_once()

        # Test when the workload container is not accessible yet.
        mock_event.reset_mock()
        mock_event.params = {"backup-id": "2023-01-01T09:00:00Z"}
        self.assertEqual(self.charm.backup._pre_restore_checks(mock_event), False)
        mock_event.fail.assert_called_once()

        # Test when the unit is in a blocked state that is not recoverable by changing
        # S3 parameters.
        mock_event.reset_mock()
        self.harness.set_can_connect("postgresql", True)
        self.charm.unit.status = BlockedStatus("fake blocked state")
        self.assertEqual(self.charm.backup._pre_restore_checks(mock_event), False)
        mock_event.fail.assert_called_once()

        # Test when the unit is in a blocked state that is recoverable by changing S3 parameters,
        # but the cluster has more than one unit.
        mock_event.reset_mock()
        self.charm.unit.status = BlockedStatus(ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE)
        _planned_units.return_value = 2
        self.assertEqual(self.charm.backup._pre_restore_checks(mock_event), False)
        mock_event.fail.assert_called_once()

        # Test when the cluster has only one unit, but it's not the leader yet.
        mock_event.reset_mock()
        _planned_units.return_value = 1
        self.assertEqual(self.charm.backup._pre_restore_checks(mock_event), False)
        mock_event.fail.assert_called_once()

        # Test when everything is ok to run a restore.
        mock_event.reset_mock()
        with self.harness.hooks_disabled():
            self.harness.set_leader()
        self.assertEqual(self.charm.backup._pre_restore_checks(mock_event), True)
        mock_event.fail.assert_not_called()

    @patch("ops.model.Container.push")
    @patch("charm.PostgreSQLBackups._retrieve_s3_parameters")
    def test_render_pgbackrest_conf_file(self, _retrieve_s3_parameters, _push):
        # Set up a mock for the `open` method, set returned data to postgresql.conf template.
        with open("templates/pgbackrest.conf.j2", "r") as f:
            mock = mock_open(read_data=f.read())

        # Test when there are missing S3 parameters.
        _retrieve_s3_parameters.return_value = [], ["bucket", "access-key", "secret-key"]

        # Patch the `open` method with our mock.
        with patch("builtins.open", mock, create=True):
            # Call the method
            self.charm.backup._render_pgbackrest_conf_file()

        mock.assert_not_called()
        _push.assert_not_called()

        # Test when all parameters are provided.
        _retrieve_s3_parameters.return_value = (
            {
                "bucket": "test-bucket",
                "access-key": "test-access-key",
                "secret-key": "test-secret-key",
                "endpoint": "https://storage.googleapis.com",
                "path": "test-path/",
                "region": "us-east-1",
                "s3-uri-style": "path",
            },
            [],
        )

        # Get the expected content from a file.
        with open("templates/pgbackrest.conf.j2") as file:
            template = Template(file.read())
        expected_content = template.render(
            enable_tls=self.charm.is_tls_enabled and len(self.charm.peer_members_endpoints) > 0,
            peer_endpoints=self.charm.peer_members_endpoints,
            path="test-path/",
            region="us-east-1",
            endpoint="https://storage.googleapis.com",
            bucket="test-bucket",
            s3_uri_style="path",
            access_key="test-access-key",
            secret_key="test-secret-key",
            stanza=self.charm.backup.stanza_name,
            storage_path=self.charm._storage_path,
            user="backup",
        )

        # Patch the `open` method with our mock.
        with patch("builtins.open", mock, create=True):
            # Call the method
            self.charm.backup._render_pgbackrest_conf_file()

        # Check the template is opened read-only in the call to open.
        self.assertEqual(mock.call_args_list[0][0], ("templates/pgbackrest.conf.j2", "r"))

        # Ensure the correct rendered template is sent to _render_file method.
        _push.assert_called_once_with(
            "/etc/pgbackrest.conf",
            expected_content,
            user="postgres",
            group="postgres",
        )

    @patch("ops.model.Container.start")
    @patch("charm.PostgresqlOperatorCharm.update_config")
    def test_restart_database(self, _update_config, _start):
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.peer_rel_id,
                self.charm.unit.name,
                {"restoring-backup": "2023-01-01T09:00:00Z"},
            )
        self.charm.backup._restart_database()

        # Assert that the backup id is not in the application relation databag anymore.
        self.assertEqual(self.harness.get_relation_data(self.peer_rel_id, self.charm.app), {})

        _update_config.assert_called_once()
        _start.assert_called_once_with("postgresql")

    @patch("charms.data_platform_libs.v0.s3.S3Requirer.get_s3_connection_info")
    def test_retrieve_s3_parameters(self, _get_s3_connection_info):
        # Test when there are missing S3 parameters.
        _get_s3_connection_info.return_value = {}
        self.assertEqual(
            self.charm.backup._retrieve_s3_parameters(),
            ({}, ["bucket", "access-key", "secret-key"]),
        )

        # Test when only the required parameters are provided.
        _get_s3_connection_info.return_value = {
            "bucket": "test-bucket",
            "access-key": "test-access-key",
            "secret-key": "test-secret-key",
        }
        self.assertEqual(
            self.charm.backup._retrieve_s3_parameters(),
            (
                {
                    "access-key": "test-access-key",
                    "bucket": "test-bucket",
                    "endpoint": "https://s3.amazonaws.com",
                    "path": "/",
                    "region": None,
                    "s3-uri-style": "host",
                    "secret-key": "test-secret-key",
                },
                [],
            ),
        )

        # Test when all parameters are provided.
        _get_s3_connection_info.return_value = {
            "bucket": " /test-bucket/ ",
            "access-key": " test-access-key ",
            "secret-key": " test-secret-key ",
            "endpoint": " https://storage.googleapis.com// ",
            "path": " test-path/ ",
            "region": " us-east-1 ",
            "s3-uri-style": " path ",
        }
        self.assertEqual(
            self.charm.backup._retrieve_s3_parameters(),
            (
                {
                    "access-key": "test-access-key",
                    "bucket": "test-bucket",
                    "endpoint": "https://storage.googleapis.com",
                    "path": "/test-path",
                    "region": "us-east-1",
                    "s3-uri-style": "path",
                    "secret-key": "test-secret-key",
                },
                [],
            ),
        )

    @patch(
        "charm.PostgreSQLBackups._is_primary_pgbackrest_service_running", new_callable=PropertyMock
    )
    @patch("charm.PostgresqlOperatorCharm.is_primary", new_callable=PropertyMock)
    @patch("ops.model.Container.restart")
    @patch("ops.model.Container.stop")
    @patch("charm.PostgresqlOperatorCharm.peer_members_endpoints", new_callable=PropertyMock)
    @patch("charm.PostgresqlOperatorCharm.is_tls_enabled", new_callable=PropertyMock)
    @patch("charm.PostgreSQLBackups._render_pgbackrest_conf_file")
    @patch("charm.PostgreSQLBackups._are_backup_settings_ok")
    def test_start_stop_pgbackrest_service(
        self,
        _are_backup_settings_ok,
        _render_pgbackrest_conf_file,
        _is_tls_enabled,
        _peer_members_endpoints,
        _stop,
        _restart,
        _is_primary,
        _is_primary_pgbackrest_service_running,
    ):
        # Test when S3 parameters are not ok (no operation, but returns success).
        _are_backup_settings_ok.return_value = (False, "fake error message")
        self.assertEqual(
            self.charm.backup.start_stop_pgbackrest_service(),
            True,
        )
        _stop.assert_not_called()
        _restart.assert_not_called()

        # Test when it was not possible to render the pgBackRest configuration file.
        _are_backup_settings_ok.return_value = (True, None)
        _render_pgbackrest_conf_file.return_value = False
        self.assertEqual(
            self.charm.backup.start_stop_pgbackrest_service(),
            False,
        )
        _stop.assert_not_called()
        _restart.assert_not_called()

        # Test when TLS is not enabled (should stop the service).
        _render_pgbackrest_conf_file.return_value = True
        _is_tls_enabled.return_value = False
        self.assertEqual(
            self.charm.backup.start_stop_pgbackrest_service(),
            True,
        )
        _stop.assert_called_once()
        _restart.assert_not_called()

        # Test when there are no replicas.
        _stop.reset_mock()
        _is_tls_enabled.return_value = True
        _peer_members_endpoints.return_value = []
        self.assertEqual(
            self.charm.backup.start_stop_pgbackrest_service(),
            True,
        )
        _stop.assert_called_once()
        _restart.assert_not_called()

        # Test when the service hasn't started in the primary yet.
        _stop.reset_mock()
        _peer_members_endpoints.return_value = ["fake-member-endpoint"]
        _is_primary.return_value = False
        _is_primary_pgbackrest_service_running.return_value = False
        self.assertEqual(
            self.charm.backup.start_stop_pgbackrest_service(),
            False,
        )
        _stop.assert_not_called()
        _restart.assert_not_called()

        # Test when the service has already started in the primary.
        _is_primary_pgbackrest_service_running.return_value = True
        self.assertEqual(
            self.charm.backup.start_stop_pgbackrest_service(),
            True,
        )
        _stop.assert_not_called()
        _restart.assert_called_once()

        # Test when this unit is the primary.
        _restart.reset_mock()
        _is_primary.return_value = True
        _is_primary_pgbackrest_service_running.return_value = False
        self.assertEqual(
            self.charm.backup.start_stop_pgbackrest_service(),
            True,
        )
        _stop.assert_not_called()
        _restart.assert_called_once()

    @patch("tempfile.NamedTemporaryFile")
    @patch("charm.PostgreSQLBackups._construct_endpoint")
    @patch("boto3.session.Session.resource")
    def test_upload_content_to_s3(self, _resource, _construct_endpoint, _named_temporary_file):
        # Set some parameters.
        content = "test-content"
        s3_path = "test-file."
        s3_parameters = {
            "bucket": "test-bucket",
            "access-key": "test-access-key",
            "secret-key": "test-secret-key",
            "endpoint": "https://s3.amazonaws.com",
            "path": "/test-path",
            "region": "us-east-1",
        }

        # Test when any exception happens.
        upload_file = _resource.return_value.Bucket.return_value.upload_file
        _resource.side_effect = ValueError
        _construct_endpoint.return_value = "https://s3.us-east-1.amazonaws.com"
        _named_temporary_file.return_value.__enter__.return_value.name = "/tmp/test-file"
        self.assertEqual(
            self.charm.backup._upload_content_to_s3(content, s3_path, s3_parameters),
            False,
        )
        _resource.assert_called_once_with("s3", endpoint_url="https://s3.us-east-1.amazonaws.com")
        _named_temporary_file.assert_not_called()
        upload_file.assert_not_called()

        _resource.reset_mock()
        _resource.side_effect = None
        upload_file.side_effect = S3UploadFailedError
        self.assertEqual(
            self.charm.backup._upload_content_to_s3(content, s3_path, s3_parameters),
            False,
        )
        _resource.assert_called_once_with("s3", endpoint_url="https://s3.us-east-1.amazonaws.com")
        _named_temporary_file.assert_called_once()
        upload_file.assert_called_once_with("/tmp/test-file", "test-path/test-file.")

        # Test when the upload succeeds
        _resource.reset_mock()
        _named_temporary_file.reset_mock()
        upload_file.reset_mock()
        upload_file.side_effect = None
        self.assertEqual(
            self.charm.backup._upload_content_to_s3(content, s3_path, s3_parameters),
            True,
        )
        _resource.assert_called_once_with("s3", endpoint_url="https://s3.us-east-1.amazonaws.com")
        _named_temporary_file.assert_called_once()
        upload_file.assert_called_once_with("/tmp/test-file", "test-path/test-file.")
