# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import datetime
import unittest
from typing import OrderedDict
from unittest.mock import PropertyMock, patch

from botocore.exceptions import ClientError
from ops import ActiveStatus, BlockedStatus
from ops.pebble import Change, ChangeError, ChangeID, ExecError
from ops.testing import Harness
from tenacity import RetryError, wait_fixed

from charm import PostgresqlOperatorCharm
from constants import PEER

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
        # Test when the unit is in a blocked state.
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
    @patch("charm.PostgreSQLBackups._execute_command")
    def test_can_use_s3_repository(
        self, _execute_command, _update_config, _member_started, _reload_patroni_configuration
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

        # Test when the unit is a replica and there is a backup from another cluster
        # in the S3 repository.
        _execute_command.return_value = (
            f'[{{"name": "another-model.{self.charm.cluster_name}"}}]',
            None,
        )
        self.assertEqual(
            self.charm.backup.can_use_s3_repository(),
            (True, None),
        )

        # Assert that the stanza name is still in the unit relation data.
        self.assertEqual(
            self.harness.get_relation_data(self.peer_rel_id, self.charm.app),
            {"stanza": self.charm.backup.stanza_name},
        )

        # Test when the unit is the leader and the workload is running.
        _member_started.return_value = True
        with self.harness.hooks_disabled():
            self.harness.set_leader()
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
        _execute_command.return_value = (
            f'[{{"name": "{self.charm.backup.stanza_name}"}}]',
            None,
        )
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
        _exec.assert_called_once_with(command, user="postgres", group="postgres")

        # Test when data files are successfully removed.
        _exec.reset_mock()
        _exec.side_effect = None
        self.charm.backup._empty_data_files()
        _exec.assert_called_once_with(command, user="postgres", group="postgres")

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
            OrderedDict[str, str](
                [("2023-01-01T09:00:00Z", "test-stanza"), ("2023-01-01T10:00:00Z", "test-stanza")]
            ),
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
    def test_initialise_stanza(
        self, _execute_command, _update_config, _, _member_started, _reload_patroni_configuration
    ):
        # Test when the unit is not the leader.
        self.charm.backup._initialise_stanza()
        _execute_command.assert_not_called()

        # Test when the unit is the leader, but it's in a blocked state
        # other than the ones can be solved by new S3 settings.
        with self.harness.hooks_disabled():
            self.harness.set_leader()
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

        # Test when the stanza creation succeeds, but the archiving is not working correctly
        # (pgBackRest check command fails).
        _execute_command.reset_mock()
        _execute_command.side_effect = [
            None,
            ExecError(
                command=stanza_creation_command, exit_code=1, stdout="", stderr="fake error"
            ),
        ]
        _member_started.return_value = True
        self.charm.backup._initialise_stanza()
        self.assertEqual(_update_config.call_count, 2)
        self.assertEqual(self.harness.get_relation_data(self.peer_rel_id, self.charm.app), {})
        self.assertEqual(_member_started.call_count, 5)
        self.assertEqual(_reload_patroni_configuration.call_count, 5)
        self.assertIsInstance(self.charm.unit.status, BlockedStatus)
        self.assertEqual(self.charm.unit.status.message, FAILED_TO_INITIALIZE_STANZA_ERROR_MESSAGE)

        # Test when the archiving is working correctly (pgBackRest check command succeeds).
        _execute_command.reset_mock()
        _update_config.reset_mock()
        _member_started.reset_mock()
        _reload_patroni_configuration.reset_mock()
        _execute_command.side_effect = None
        self.charm.backup._initialise_stanza()
        self.assertEqual(
            self.harness.get_relation_data(self.peer_rel_id, self.charm.app),
            {"stanza": self.charm.backup.stanza_name},
        )
        _update_config.assert_called_once()
        _member_started.assert_called_once()
        _reload_patroni_configuration.assert_called_once()
        self.assertIsInstance(self.charm.unit.status, ActiveStatus)

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

        # Test when the pgBackRest fails to contact the primary server.
        _get_primary.side_effect = None
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
