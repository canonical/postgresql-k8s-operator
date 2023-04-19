# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import unittest
from unittest.mock import patch

from ops.testing import Harness

from charm import PostgresqlOperatorCharm
from constants import PEER


class TestPostgreSQLBackups(unittest.TestCase):
    @patch("charm.KubernetesServicePatch", lambda x, y: None)
    def setUp(self):
        self.harness = Harness(PostgresqlOperatorCharm)
        self.addCleanup(self.harness.cleanup)

        # Set up the initial relation and hooks.
        self.peer_rel_id = self.harness.add_relation(PEER, "postgresql-k8s")
        self.harness.add_relation_unit(self.peer_rel_id, "postgresql-k8s/0")
        self.harness.begin()
        self.charm = self.harness.charm

    def test_are_s3_parameters_ok(self):
        pass

    def test_can_unit_perform_backup(self):
        pass

    def test_construct_endpoint(self):
        pass

    def test_empty_data_files(self):
        pass

    def test_change_connectivity_to_database(self):
        pass

    def test_execute_command(self):
        pass

    def test_format_backup_list(self):
        pass

    def test_generate_backup_list_output(self):
        pass

    def test_list_backups(self):
        pass

    def test_initialise_stanza(self):
        pass

    def test_is_primary_pgbackrest_service_running(self):
        pass

    def test_on_backup_s3_credential_changed(self):
        pass

    def test_on_restore_s3_credential_changed(self):
        pass

    def test_on_create_backup_action(self):
        pass

    def test_on_list_backups_action(self):
        pass

    def test_on_restore_action(self):
        pass

    def test_pre_restore_checks(self):
        pass

    def test_render_pgbackrest_conf_file(self):
        pass

    def test_restart_database(self):
        pass

    def test_retrieve_s3_parameters(self):
        pass

    def test_start_stop_pgbackrest_service(self):
        pass

    def test_upload_content_to_s3(self):
        pass
