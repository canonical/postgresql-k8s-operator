#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

from signal import SIGHUP
from unittest.mock import MagicMock, Mock, PropertyMock, mock_open, patch

import pytest
import requests
import tenacity
from jinja2 import Template
from ops.testing import Harness
from tenacity import RetryError, stop_after_delay, wait_fixed

from charm import PostgresqlOperatorCharm
from constants import REWIND_USER
from patroni import PATRONI_TIMEOUT, Patroni, SwitchoverFailedError, SwitchoverNotSyncError
from tests.helpers import STORAGE_PATH


@pytest.fixture(autouse=True)
def harness():
    harness = Harness(PostgresqlOperatorCharm)
    harness.begin()
    yield harness
    harness.cleanup()


@pytest.fixture(autouse=True)
def patroni(harness):
    # Setup Patroni wrapper.
    patroni = Patroni(
        harness.charm,
        "postgresql-k8s-0",
        ["postgresql-k8s-0", "postgresql-k8s-1", "postgresql-k8s-2"],
        "postgresql-k8s-primary.dev.svc.cluster.local",
        "test-model",
        STORAGE_PATH,
        "superuser-password",
        "replication-password",
        "rewind-password",
        "patroni-password",
    )
    root = harness.get_filesystem_root("postgresql")
    (root / "var" / "log" / "postgresql").mkdir(parents=True, exist_ok=True)

    yield patroni


# This method will be used by the mock to replace requests.get
def mocked_requests_get(*args, **kwargs):
    class MockResponse:
        def __init__(self, json_data):
            self.json_data = json_data

        def json(self):
            return self.json_data

    data = {
        "http://server1/cluster": {
            "members": [
                {"name": "postgresql-k8s-0", "host": "1.1.1.1", "role": "leader", "lag": "1"}
            ]
        },
        "http://server1/health": {"state": "running"},
        "http://server4/cluster": {"members": []},
    }
    if args[0] in data:
        return MockResponse(data[args[0]])

    raise requests.exceptions.Timeout()


def test_dict_to_hba_string(harness, patroni):
    mock_data = {
        "ldapbasedn": "dc=example,dc=net",
        "ldapbinddn": "cn=serviceuser,dc=example,dc=net",
        "ldapbindpasswd": "password",
        "ldaptls": False,
        "ldapurl": "ldap://0.0.0.0:3893",
    }

    assert patroni._dict_to_hba_string(mock_data) == (
        'ldapbasedn="dc=example,dc=net" '
        'ldapbinddn="cn=serviceuser,dc=example,dc=net" '
        'ldapbindpasswd="password" '
        "ldaptls=0 "
        'ldapurl="ldap://0.0.0.0:3893"'
    )


def test_get_primary(harness, patroni):
    with (
        patch(
            "charm.Patroni.parallel_patroni_get_request", return_value=None
        ) as _parallel_patroni_get_request,
    ):
        # No primary if no members
        assert patroni.get_primary() is None

        _parallel_patroni_get_request.return_value = {
            "members": [
                {
                    "name": "postgresql-1",
                    "role": "replica",
                },
                {
                    "name": "postgresql-0",
                    "role": "leader",
                },
            ]
        }
        # Test using the current Patroni URL.
        assert patroni.get_primary() == "postgresql-0"

        # Test requesting the primary in the unit name pattern.
        assert patroni.get_primary(unit_name_pattern=True) == "postgresql/0"


def test_is_creating_backup(harness, patroni):
    with patch("charm.Patroni.cluster_status") as _cluster_status:
        # Test when one member is creating a backup.
        _cluster_status.return_value = [
            {"name": "postgresql-0"},
            {"name": "postgresql-1", "tags": {"is_creating_backup": True}},
        ]
        assert patroni.is_creating_backup

        # Test when no member is creating a backup.
        del patroni.cached_cluster_status
        _cluster_status.return_value = [{"name": "postgresql-0"}, {"name": "postgresql-1"}]
        assert not patroni.is_creating_backup


def test_is_replication_healthy(harness, patroni):
    with (
        patch("requests.get") as _get,
        patch("charm.Patroni.get_primary"),
        patch("patroni.stop_after_delay", return_value=stop_after_delay(0)),
    ):
        # Test when replication is healthy.
        _get.return_value.status_code = 200
        assert patroni.is_replication_healthy

        # Test when replication is not healthy.
        _get.side_effect = [
            MagicMock(status_code=200),
            MagicMock(status_code=200),
            MagicMock(status_code=503),
        ]
        assert not patroni.is_replication_healthy


def test_member_streaming(harness, patroni):
    with (
        patch("requests.get") as _get,
        patch("patroni.stop_after_delay", return_value=stop_after_delay(0)),
    ):
        # Test when the member is streaming from primary.
        _get.return_value.json.return_value = {"replication_state": "streaming"}
        assert patroni.member_streaming

        # Test when the member is not streaming from primary.
        _get.return_value.json.return_value = {"replication_state": "running"}
        assert not patroni.member_streaming

        _get.return_value.json.return_value = {}
        assert not patroni.member_streaming

        # Test when an error happens.
        _get.side_effect = RetryError
        assert not patroni.member_streaming


def test_render_file(harness, patroni):
    with (
        patch("os.chmod") as _chmod,
        patch("os.chown") as _chown,
        patch("pwd.getpwnam") as _pwnam,
        patch("tempfile.NamedTemporaryFile") as _temp_file,
    ):
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
            patroni._render_file(filename, "rendered-content", 0o640)

        # Check the rendered file is opened with "w+" mode.
        assert mock.call_args_list[0][0] == (filename, "w+")
        # Ensure that the correct user is lookup up.
        _pwnam.assert_called_with("postgres")
        # Ensure the file is chmod'd correctly.
        _chmod.assert_called_with(filename, 0o640)
        # Ensure the file is chown'd correctly.
        _chown.assert_called_with(filename, uid=35, gid=35)


def test_render_patroni_yml_file(harness, patroni):
    with (
        patch(
            "charm.Patroni.rock_postgresql_version", new_callable=PropertyMock
        ) as _rock_postgresql_version,
        patch("charm.Patroni._render_file") as _render_file,
    ):
        _rock_postgresql_version.return_value = "14.7"

        # Get the expected content from a file.
        with open("templates/patroni.yml.j2") as file:
            template = Template(file.read())
        expected_content = template.render(
            endpoint=patroni._endpoint,
            endpoints=patroni._endpoints,
            namespace=patroni._namespace,
            storage_path=patroni._storage_path,
            superuser_password=patroni._superuser_password,
            replication_password=patroni._replication_password,
            rewind_user=REWIND_USER,
            rewind_password=patroni._rewind_password,
            synchronous_node_count=0,
            maximum_lag_on_failover=1048576,
            version="14",
            patroni_password=patroni._patroni_password,
        )

        # Setup a mock for the `open` method, set returned data to postgresql.conf template.
        with open("templates/patroni.yml.j2") as f:
            mock = mock_open(read_data=f.read())

        # Patch the `open` method with our mock.
        with patch("builtins.open", mock, create=True):
            # Call the method
            patroni.render_patroni_yml_file(enable_tls=False)

        # Check the template is opened read-only in the call to open.
        assert mock.call_args_list[0][0] == ("templates/patroni.yml.j2",)
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
            endpoint=patroni._endpoint,
            endpoints=patroni._endpoints,
            namespace=patroni._namespace,
            storage_path=patroni._storage_path,
            superuser_password=patroni._superuser_password,
            replication_password=patroni._replication_password,
            rewind_user=REWIND_USER,
            rewind_password=patroni._rewind_password,
            synchronous_node_count=0,
            maximum_lag_on_failover=1048576,
            version="14",
            patroni_password=patroni._patroni_password,
        )
        assert expected_content_with_tls != expected_content

        # Patch the `open` method with our mock.
        with patch("builtins.open", mock, create=True):
            # Call the method
            patroni.render_patroni_yml_file(enable_tls=True)

        # Ensure the correct rendered template is sent to _render_file method.
        _render_file.assert_called_once_with(
            f"{STORAGE_PATH}/patroni.yml",
            expected_content_with_tls,
            0o644,
        )

        # Also, ensure the right parameters are in the expected content
        # (as it was already validated with the above render file call).
        assert "ssl: on" in expected_content_with_tls
        assert "ssl_ca_file: /var/lib/postgresql/data/ca.pem" in expected_content_with_tls
        assert "ssl_cert_file: /var/lib/postgresql/data/cert.pem" in expected_content_with_tls
        assert "ssl_key_file: /var/lib/postgresql/data/key.pem" in expected_content_with_tls


def test_primary_endpoint_ready(harness, patroni):
    with (
        patch("patroni.stop_after_delay", return_value=stop_after_delay(0)),
        patch("patroni.wait_fixed", return_value=wait_fixed(0)),
        patch("requests.get") as _get,
    ):
        # Test with an issue when trying to connect to the Patroni API.
        _get.side_effect = RetryError
        assert not patroni.primary_endpoint_ready

        # Mock the request return values.
        _get.side_effect = None
        _get.return_value.json.return_value = {"state": "stopped"}

        # Test with the primary endpoint not ready yet.
        assert not patroni.primary_endpoint_ready

        # Test with the primary endpoint ready.
        _get.return_value.json.return_value = {"state": "running"}
        assert patroni.primary_endpoint_ready


def test_switchover(harness, patroni):
    with (
        patch("patroni.stop_after_delay", return_value=tenacity.stop_after_delay(0)),
        patch("requests.post") as _post,
        patch("patroni.Patroni.get_primary") as _get_primary,
    ):
        # Test a successful switchover.
        _get_primary.side_effect = ["postgresql-k8s-0", "postgresql-k8s-1"]
        response = _post.return_value
        response.status_code = 200
        patroni.switchover()
        _post.assert_called_once_with(
            "http://postgresql-k8s-0:8008/switchover",
            json={"leader": "postgresql-k8s-0", "candidate": None},
            verify=True,
            auth=patroni._patroni_auth,
            timeout=PATRONI_TIMEOUT,
        )

        # Test a successful switchover with a candidate name.
        _post.reset_mock()
        _get_primary.side_effect = ["postgresql-k8s-0", "postgresql-k8s-2"]
        patroni.switchover("postgresql-k8s/2")
        _post.assert_called_once_with(
            "http://postgresql-k8s-0:8008/switchover",
            json={"leader": "postgresql-k8s-0", "candidate": "postgresql-k8s-2"},
            verify=True,
            auth=patroni._patroni_auth,
            timeout=PATRONI_TIMEOUT,
        )

        # Test failed switchovers.
        _post.reset_mock()
        _get_primary.side_effect = ["postgresql-k8s-0", "postgresql-k8s-1"]
        with pytest.raises(SwitchoverFailedError):
            patroni.switchover("postgresql-k8s/2")
            assert False
        _post.assert_called_once_with(
            "http://postgresql-k8s-0:8008/switchover",
            json={"leader": "postgresql-k8s-0", "candidate": "postgresql-k8s-2"},
            verify=True,
            auth=patroni._patroni_auth,
            timeout=PATRONI_TIMEOUT,
        )

        _post.reset_mock()
        _get_primary.side_effect = ["postgresql-k8s-0", "postgresql-k8s-2"]
        response.status_code = 400
        with pytest.raises(SwitchoverFailedError):
            patroni.switchover("postgresql-k8s/2")
            assert False
        _post.assert_called_once_with(
            "http://postgresql-k8s-0:8008/switchover",
            json={"leader": "postgresql-k8s-0", "candidate": "postgresql-k8s-2"},
            verify=True,
            auth=patroni._patroni_auth,
            timeout=PATRONI_TIMEOUT,
        )

        # Test candidate, not sync
        response = _post.return_value
        response.status_code = 412
        response.text = "candidate name does not match with sync_standby"
        with pytest.raises(SwitchoverNotSyncError):
            patroni.switchover("candidate")
            assert False


def test_member_started_true(patroni):
    with (
        patch("patroni.requests.get") as _get,
        patch("patroni.stop_after_delay", return_value=tenacity.stop_after_delay(0)),
        patch("patroni.wait_fixed", return_value=tenacity.wait_fixed(0)),
    ):
        _get.return_value.json.return_value = {"state": "running"}

        assert patroni.member_started

        _get.assert_called_once_with(
            "http://postgresql-k8s-0:8008/health",
            verify=True,
            auth=patroni._patroni_auth,
            timeout=PATRONI_TIMEOUT,
        )


def test_member_started_false(patroni):
    with (
        patch("patroni.requests.get") as _get,
        patch("patroni.stop_after_delay", return_value=tenacity.stop_after_delay(0)),
        patch("patroni.wait_fixed", return_value=tenacity.wait_fixed(0)),
    ):
        _get.return_value.json.return_value = {"state": "stopped"}

        assert not patroni.member_started

        _get.assert_called_once_with(
            "http://postgresql-k8s-0:8008/health",
            verify=True,
            auth=patroni._patroni_auth,
            timeout=PATRONI_TIMEOUT,
        )


def test_member_started_error(patroni):
    with (
        patch("patroni.requests.get") as _get,
        patch("patroni.stop_after_delay", return_value=tenacity.stop_after_delay(0)),
        patch("patroni.wait_fixed", return_value=tenacity.wait_fixed(0)),
    ):
        _get.side_effect = Exception

        assert not patroni.member_started

        _get.assert_called_once_with(
            "http://postgresql-k8s-0:8008/health",
            verify=True,
            auth=patroni._patroni_auth,
            timeout=PATRONI_TIMEOUT,
        )


def test_last_postgresql_logs(harness, patroni):
    # Empty if container can't connect
    harness.set_can_connect("postgresql", False)
    assert patroni.last_postgresql_logs() == ""

    # Test when there are no files to read.
    harness.set_can_connect("postgresql", True)
    assert patroni.last_postgresql_logs() == ""

    # Test when there are multiple files in the logs directory.
    root = harness.get_filesystem_root("postgresql")
    with (root / "var" / "log" / "postgresql" / "postgresql.1.log").open("w") as fd:
        fd.write("fake-logs1")
    with (root / "var" / "log" / "postgresql" / "postgresql.2.log").open("w") as fd:
        fd.write("fake-logs2")
    with (root / "var" / "log" / "postgresql" / "postgresql.3.log").open("w") as fd:
        fd.write("fake-logs3")

    assert patroni.last_postgresql_logs() == "fake-logs3"

    # Test when the charm fails to read the logs.
    (root / "var" / "log" / "postgresql" / "postgresql.1.log").unlink()
    (root / "var" / "log" / "postgresql" / "postgresql.2.log").unlink()
    (root / "var" / "log" / "postgresql" / "postgresql.3.log").unlink()
    (root / "var" / "log" / "postgresql").rmdir()
    assert patroni.last_postgresql_logs() == ""


def test_update_synchronous_node_count(harness, patroni):
    with (
        patch("patroni.stop_after_delay", return_value=stop_after_delay(0)) as _wait_fixed,
        patch("patroni.wait_fixed", return_value=wait_fixed(0)) as _wait_fixed,
        patch("requests.patch") as _patch,
    ):
        response = _patch.return_value
        response.status_code = 200

        patroni.update_synchronous_node_count()

        _patch.assert_called_once_with(
            "http://postgresql-k8s-0:8008/config",
            json={"synchronous_node_count": 0, "synchronous_mode_strict": False},
            verify=True,
            auth=patroni._patroni_auth,
            timeout=10,
        )

        # Test when the request fails.
        response.status_code = 500
        with pytest.raises(RetryError):
            patroni.update_synchronous_node_count()
            assert False


def test_set_failsafe_mode(harness, patroni):
    with (
        patch("requests.patch") as _patch,
    ):
        patroni.set_failsafe_mode()

        _patch.assert_called_once_with(
            "http://postgresql-k8s-0:8008/config",
            json={"failsafe_mode": True},
            verify=True,
            auth=patroni._patroni_auth,
            timeout=10,
        )


def test_reload_patroni_configuration(harness, patroni):
    with (
        patch("ops.model.Container.send_signal") as _send_signal,
        patch("ops.model.Container.pebble") as _pebble,
    ):
        # Can't connect
        harness.set_can_connect("postgresql", False)

        patroni.reload_patroni_configuration()

        assert not _send_signal.called
        assert not _pebble.get_services.called

        # No service
        harness.set_can_connect("postgresql", True)
        _pebble.get_services.return_value = []

        patroni.reload_patroni_configuration()

        assert not _send_signal.called
        _pebble.get_services.assert_called_once_with(names=["postgresql"])
        _pebble.get_services.reset_mock()

        # Not running
        mock_svc = Mock()
        mock_svc.is_running.return_value = False
        _pebble.get_services.return_value = [mock_svc]

        patroni.reload_patroni_configuration()

        assert not _send_signal.called
        _pebble.get_services.assert_called_once_with(names=["postgresql"])
        _pebble.get_services.reset_mock()

        # Reload
        mock_svc.is_running.return_value = True

        patroni.reload_patroni_configuration()

        _send_signal.assert_called_once_with(SIGHUP, "postgresql")
