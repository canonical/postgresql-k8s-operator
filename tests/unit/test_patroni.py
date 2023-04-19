#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import unittest
from unittest.mock import PropertyMock, mock_open, patch

from jinja2 import Template
from ops.testing import Harness
from tenacity import RetryError

from charm import PostgresqlOperatorCharm
from constants import REWIND_USER
from patroni import Patroni
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
            ["postgresql-k8s-0"],
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

    @patch("requests.get")
    def test_primary_endpoint_ready(self, _get):
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
