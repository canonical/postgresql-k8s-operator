#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import unittest
from unittest.mock import mock_open, patch

from jinja2 import Template

from patroni import Patroni
from tests.helpers import STORAGE_PATH


class TestPatroni(unittest.TestCase):
    def setUp(self):
        # Setup Patroni wrapper.
        self.patroni = Patroni("postgresql-k8s-0", "postgresql-k8s-0", "test-model", STORAGE_PATH)

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
        _get.assert_called_once_with("http://postgresql-k8s-0:8008/cluster")

        # Test returning unit name.
        _get.reset_mock()
        primary = self.patroni.get_primary(unit_name_pattern=True)
        self.assertEqual(primary, "postgresql-k8s/1")
        _get.assert_called_once_with("http://postgresql-k8s-0:8008/cluster")

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

    @patch("charm.Patroni._render_file")
    def test_render_patroni_yml_file(self, _render_file):
        # Get the expected content from a file.
        with open("templates/patroni.yml.j2") as file:
            template = Template(file.read())
        expected_content = template.render(
            endpoint=self.patroni._endpoint,
            endpoints=self.patroni._endpoints,
            namespace=self.patroni._namespace,
            storage_path=self.patroni._storage_path,
        )

        # Setup a mock for the `open` method, set returned data to postgresql.conf template.
        with open("templates/patroni.yml.j2", "r") as f:
            mock = mock_open(read_data=f.read())

        # Patch the `open` method with our mock.
        with patch("builtins.open", mock, create=True):
            # Call the method
            self.patroni.render_patroni_yml_file()

        # Check the template is opened read-only in the call to open.
        self.assertEqual(mock.call_args_list[0][0], ("templates/patroni.yml.j2", "r"))
        # Ensure the correct rendered template is sent to _render_file method.
        _render_file.assert_called_once_with(
            f"{STORAGE_PATH}/patroni.yml",
            expected_content,
            0o644,
        )

    @patch("charm.Patroni._render_file")
    def test_render_postgresql_conf_file(self, _render_file):
        # Get the expected content from a file.
        with open("tests/data/postgresql.conf") as file:
            expected_content = file.read()

        # Setup a mock for the `open` method, set returned data to postgresql.conf template.
        with open("templates/postgresql.conf.j2", "r") as f:
            mock = mock_open(read_data=f.read())

        # Patch the `open` method with our mock.
        with patch("builtins.open", mock, create=True):
            # Call the method
            self.patroni.render_postgresql_conf_file()

        # Check the template is opened read-only in the call to open.
        self.assertEqual(mock.call_args_list[0][0], ("templates/postgresql.conf.j2", "r"))
        # Ensure the correct rendered template is sent to _render_file method.
        _render_file.assert_called_once_with(
            f"{STORAGE_PATH}/postgresql-k8s-operator.conf",
            expected_content,
            0o644,
        )
