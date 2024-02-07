#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import unittest
from unittest.mock import MagicMock, PropertyMock, mock_open, patch

import tenacity
from jinja2 import Template
from ops.testing import Harness
from tenacity import RetryError, stop_after_delay, wait_fixed

from charm import PostgresqlOperatorCharm
from constants import REWIND_USER
from patroni import Patroni, SwitchoverFailedError
from tests.helpers import STORAGE_PATH, patch_network_get


class TestPatroni(unittest.TestCase):
    @patch("charm.KubernetesServicePatch", lambda x, y: None)
    @patch_network_get(private_address="1.1.1.1")
    def setUp(self):
        self.harness = Harness(PostgresqlOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()
        self.charm = self.harness.charm

        # Setup Patroni wrapper.
        self.patroni = Patroni(
            self.charm,
            "postgresql-k8s-0",
            ["postgresql-k8s-0", "postgresql-k8s-1", "postgresql-k8s-2"],
            "postgresql-k8s-primary.dev.svc.cluster.local",
            "test-model",
            STORAGE_PATH,
            "superuser-password",
            "replication-password",
            "rewind-password",
            False,
        )

    @patch("requests.get")
    def test_get_primary(self, _get):
        # Mock Patroni cluster API.
        _get.return_value.json.return_value = {
            "members": [
                {"name": "postgresql-k8s-0", "role": "replica"},
                {"name": "postgresql-k8s-1", "role": "leader"},
                {"name": "postgresql-k8s-2", "role": "replica"},
            ]
        }

        # Test returning pod name.
        primary = self.patroni.get_primary()
        self.assertEqual(primary, "postgresql-k8s-1")
        _get.assert_called_once_with("http://postgresql-k8s-0:8008/cluster", verify=True)

        # Test returning unit name.
        _get.reset_mock()
        primary = self.patroni.get_primary(unit_name_pattern=True)
        self.assertEqual(primary, "postgresql-k8s/1")
        _get.assert_called_once_with("http://postgresql-k8s-0:8008/cluster", verify=True)

    @patch("requests.get")
    def test_is_creating_backup(self, _get):
        # Test when one member is creating a backup.
        response = _get.return_value
        response.json.return_value = {
            "members": [
                {"name": "postgresql-k8s-0"},
                {"name": "postgresql-k8s-1", "tags": {"is_creating_backup": True}},
            ]
        }
        self.assertTrue(self.patroni.is_creating_backup)

        # Test when no member is creating a backup.
        response.json.return_value = {
            "members": [{"name": "postgresql-k8s-0"}, {"name": "postgresql-k8s-1"}]
        }
        self.assertFalse(self.patroni.is_creating_backup)

    @patch("requests.get")
    @patch("charm.Patroni.get_primary")
    @patch("patroni.stop_after_delay", return_value=stop_after_delay(0))
    def test_is_replication_healthy(self, _, __, _get):
        # Test when replication is healthy.
        _get.return_value.status_code = 200
        self.assertTrue(self.patroni.is_replication_healthy)

        # Test when replication is not healthy.
        _get.side_effect = [
            MagicMock(status_code=200),
            MagicMock(status_code=200),
            MagicMock(status_code=503),
        ]
        self.assertFalse(self.patroni.is_replication_healthy)

    @patch("requests.get")
    @patch("patroni.stop_after_delay", return_value=stop_after_delay(0))
    def test_member_streaming(self, _, _get):
        # Test when the member is streaming from primary.
        _get.return_value.json.return_value = {"replication_state": "streaming"}
        self.assertTrue(self.patroni.member_streaming)

        # Test when the member is not streaming from primary.
        _get.return_value.json.return_value = {"replication_state": "running"}
        self.assertFalse(self.patroni.member_streaming)

        _get.return_value.json.return_value = {}
        self.assertFalse(self.patroni.member_streaming)

        # Test when an error happens.
        _get.side_effect = RetryError
        self.assertFalse(self.patroni.member_streaming)

    @patch("os.chmod")
    @patch("os.chown")
    @patch("pwd.getpwnam")
    @patch("tempfile.NamedTemporaryFile")
    def test_render_file(self, _temp_file, _pwnam, _chown, _chmod):
        # Set a mocked temporary filename.
        filename = "/tmp/temporaryfilename"
        _temp_file.return_value.name = filename
        # Setup a mock for the `open` method.
        mock = mock_open()
        # Patch the `open` method with our mock.
        with patch("builtins.open", mock, create=True):
            # Set the uid/gid return values for lookup of 'postgres' user.
            _pwnam.return_value.pw_uid = 35
            _pwnam.return_value.pw_gid = 35
            # Call the method using a temporary configuration file.
            self.patroni._render_file(filename, "rendered-content", 0o640)

        # Check the rendered file is opened with "w+" mode.
        self.assertEqual(mock.call_args_list[0][0], (filename, "w+"))
        # Ensure that the correct user is lookup up.
        _pwnam.assert_called_with("postgres")
        # Ensure the file is chmod'd correctly.
        _chmod.assert_called_with(filename, 0o640)
        # Ensure the file is chown'd correctly.
        _chown.assert_called_with(filename, uid=35, gid=35)

    @patch("charm.Patroni.rock_postgresql_version", new_callable=PropertyMock)
    @patch("charm.Patroni._render_file")
    def test_render_patroni_yml_file(self, _render_file, _rock_postgresql_version):
        _rock_postgresql_version.return_value = "14.7"

        # Get the expected content from a file.
        with open("templates/patroni.yml.j2") as file:
            template = Template(file.read())
        expected_content = template.render(
            endpoint=self.patroni._endpoint,
            endpoints=self.patroni._endpoints,
            namespace=self.patroni._namespace,
            storage_path=self.patroni._storage_path,
            superuser_password=self.patroni._superuser_password,
            replication_password=self.patroni._replication_password,
            rewind_user=REWIND_USER,
            rewind_password=self.patroni._rewind_password,
            minority_count=self.patroni._members_count // 2,
            version="14",
        )

        # Setup a mock for the `open` method, set returned data to postgresql.conf template.
        with open("templates/patroni.yml.j2", "r") as f:
            mock = mock_open(read_data=f.read())

        # Patch the `open` method with our mock.
        with patch("builtins.open", mock, create=True):
            # Call the method
            self.patroni.render_patroni_yml_file(enable_tls=False)

        # Check the template is opened read-only in the call to open.
        self.assertEqual(mock.call_args_list[0][0], ("templates/patroni.yml.j2", "r"))
        # Ensure the correct rendered template is sent to _render_file method.
        _render_file.assert_called_once_with(
            f"{STORAGE_PATH}/patroni.yml",
            expected_content,
            0o644,
        )

        # Then test the rendering of the file with TLS enabled.
        _render_file.reset_mock()
        expected_content_with_tls = template.render(
            enable_tls=True,
            endpoint=self.patroni._endpoint,
            endpoints=self.patroni._endpoints,
            namespace=self.patroni._namespace,
            storage_path=self.patroni._storage_path,
            superuser_password=self.patroni._superuser_password,
            replication_password=self.patroni._replication_password,
            rewind_user=REWIND_USER,
            rewind_password=self.patroni._rewind_password,
            minority_count=self.patroni._members_count // 2,
            version="14",
        )
        self.assertNotEqual(expected_content_with_tls, expected_content)

        # Patch the `open` method with our mock.
        with patch("builtins.open", mock, create=True):
            # Call the method
            self.patroni.render_patroni_yml_file(enable_tls=True)

        # Ensure the correct rendered template is sent to _render_file method.
        _render_file.assert_called_once_with(
            f"{STORAGE_PATH}/patroni.yml",
            expected_content_with_tls,
            0o644,
        )

        # Also, ensure the right parameters are in the expected content
        # (as it was already validated with the above render file call).
        self.assertIn("ssl: on", expected_content_with_tls)
        self.assertIn("ssl_ca_file: /var/lib/postgresql/data/ca.pem", expected_content_with_tls)
        self.assertIn(
            "ssl_cert_file: /var/lib/postgresql/data/cert.pem", expected_content_with_tls
        )
        self.assertIn("ssl_key_file: /var/lib/postgresql/data/key.pem", expected_content_with_tls)

    @patch("patroni.stop_after_delay", return_value=stop_after_delay(0))
    @patch("patroni.wait_fixed", return_value=wait_fixed(0))
    @patch("requests.get")
    def test_primary_endpoint_ready(self, _get, _, __):
        # Test with an issue when trying to connect to the Patroni API.
        _get.side_effect = RetryError
        self.assertFalse(self.patroni.primary_endpoint_ready)

        # Mock the request return values.
        _get.side_effect = None
        _get.return_value.json.return_value = {"state": "stopped"}

        # Test with the primary endpoint not ready yet.
        self.assertFalse(self.patroni.primary_endpoint_ready)

        # Test with the primary endpoint ready.
        _get.return_value.json.return_value = {"state": "running"}
        self.assertTrue(self.patroni.primary_endpoint_ready)

    @patch("patroni.stop_after_delay", return_value=tenacity.stop_after_delay(0))
    @patch("requests.post")
    @patch("patroni.Patroni.get_primary")
    def test_switchover(self, _get_primary, _post, __):
        # Test a successful switchover.
        _get_primary.side_effect = ["postgresql-k8s-0", "postgresql-k8s-1"]
        response = _post.return_value
        response.status_code = 200
        self.patroni.switchover()
        _post.assert_called_once_with(
            "http://postgresql-k8s-0:8008/switchover",
            json={"leader": "postgresql-k8s-0", "candidate": None},
            verify=True,
        )

        # Test a successful switchover with a candidate name.
        _post.reset_mock()
        _get_primary.side_effect = ["postgresql-k8s-0", "postgresql-k8s-2"]
        self.patroni.switchover("postgresql-k8s/2")
        _post.assert_called_once_with(
            "http://postgresql-k8s-0:8008/switchover",
            json={"leader": "postgresql-k8s-0", "candidate": "postgresql-k8s-2"},
            verify=True,
        )

        # Test failed switchovers.
        _post.reset_mock()
        _get_primary.side_effect = ["postgresql-k8s-0", "postgresql-k8s-1"]
        with self.assertRaises(SwitchoverFailedError):
            self.patroni.switchover("postgresql-k8s/2")
        _post.assert_called_once_with(
            "http://postgresql-k8s-0:8008/switchover",
            json={"leader": "postgresql-k8s-0", "candidate": "postgresql-k8s-2"},
            verify=True,
        )

        _post.reset_mock()
        _get_primary.side_effect = ["postgresql-k8s-0", "postgresql-k8s-2"]
        response.status_code = 400
        with self.assertRaises(SwitchoverFailedError):
            self.patroni.switchover("postgresql-k8s/2")
        _post.assert_called_once_with(
            "http://postgresql-k8s-0:8008/switchover",
            json={"leader": "postgresql-k8s-0", "candidate": "postgresql-k8s-2"},
            verify=True,
        )
