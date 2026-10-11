# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, Mock, PropertyMock, call, patch, sentinel

import pytest
from ops import JujuVersion, ModelError
from ops.model import (
    MaintenanceStatus,
)
from ops.pebble import FileType
from ops.testing import Harness
from single_kernel_postgresql.config.literals import CERTS_PATH, PEER_RELATION

from charm import PostgresqlOperatorCharm

POSTGRESQL_CONTAINER = "postgresql"
POSTGRESQL_SERVICE = "postgresql"
LDAP_SYNC_SERVICE = "ldap-sync"
METRICS_SERVICE = "metrics_server"
PGBACKREST_METRICS_SERVICE = "pgbackrest_metrics_service"
PGBACKREST_SERVER_SERVICE = "pgbackrest server"
ROTATE_LOGS_SERVICE = "rotate-logs"

# used for assert functions
tc = TestCase()


@pytest.fixture(autouse=True)
def harness():
    harness = Harness(
        PostgresqlOperatorCharm,
        meta=Path("metadata.yaml").read_text(),
        actions=Path("actions.yaml").read_text(),
    )
    harness.handle_exec("postgresql", ["locale", "-a"], result="C")

    # The lib resource reads build a lightkube client; mock the sync client the
    # way the retired charm-side backup suite leaked it for the whole module.
    with patch("lightkube.core.client.GenericSyncClient"):
        harness.add_relation(PEER_RELATION, "postgresql-k8s")
        harness.begin()
        harness.add_relation("restart", harness.charm.app.name)
        yield harness
        harness.cleanup()


def test_set_ports():
    with (
        patch("ops.model.Model.juju_version", new_callable=PropertyMock) as _juju_version,
        patch("charm.PostgresqlOperatorCharm.unit") as _unit,
    ):
        _juju_version.return_value.major = 3
        harness = Harness(
            PostgresqlOperatorCharm,
            meta=Path("metadata.yaml").read_text(),
            actions=Path("actions.yaml").read_text(),
        )
        harness.begin()
        _unit.set_ports.assert_called_once_with(5432, 8008)

        harness.cleanup()


def test_on_postgresql_pebble_ready_no_connection(harness):
    with (
        patch("single_kernel_postgresql.charms.k8s_charm.PostgreSQLK8sCharm.create_pgdata"),
    ):
        mock_event = MagicMock()
        mock_event.workload = harness.model.unit.get_container(POSTGRESQL_CONTAINER)

        harness.charm.postgresql_events_handler._on_postgresql_pebble_ready(mock_event)

        # Event was deferred and status is still maintenance
        mock_event.defer.assert_called_once()
        mock_event.set_results.assert_not_called()
        assert isinstance(harness.model.unit.status, MaintenanceStatus)


def test_on_peer_relation_departed(harness):
    with (
        patch(
            "single_kernel_postgresql.managers.cluster_membership.ClusterMembershipManager.get_endpoints_to_remove",
            return_value=[sentinel.units],
        ) as _get_endpoints_to_remove,
        patch(
            "single_kernel_postgresql.core.peer_relation.PostgreSQLApplication.is_cluster_initialised",
            new_callable=PropertyMock,
        ) as _app_peer_data,
        patch(
            "single_kernel_postgresql.managers.cluster_membership.ClusterMembershipManager.remove_endpoint"
        ) as _remove_from_endpoints,
        patch(
            "single_kernel_postgresql.managers.cluster_membership.ClusterMembershipManager.updated_synchronous_node_count",
            return_value=True,
        ),
    ):
        # Early exit if not leader
        event = Mock()
        harness.charm.postgresql_events_handler._on_peer_relation_departed(event)
        assert not _get_endpoints_to_remove.called

        # Defer if cluster is not initialised
        _app_peer_data.return_value = False
        with harness.hooks_disabled():
            harness.set_leader()
        harness.charm.postgresql_events_handler._on_peer_relation_departed(event)
        event.defer.assert_called_once_with()

        _app_peer_data.return_value = True
        harness.charm.postgresql_events_handler._on_peer_relation_departed(event)
        _get_endpoints_to_remove.assert_called_once_with()
        _remove_from_endpoints.assert_called_once_with(sentinel.units)


def test_validate_config_options(harness):
    with patch(
        "single_kernel_postgresql.charms.k8s_charm.PostgreSQLK8sCharm.postgresql",
        new_callable=PropertyMock,
    ) as _charm_lib:
        harness.set_can_connect(POSTGRESQL_CONTAINER, True)
        _charm_lib.return_value.get_postgresql_text_search_configs.return_value = []
        _charm_lib.return_value.validate_date_style.return_value = []
        _charm_lib.return_value.validate_group_map.return_value = False
        _charm_lib.return_value.get_postgresql_timezones.return_value = []

        # Test instance-default-text-search-config exception
        with harness.hooks_disabled():
            harness.update_config({"instance-default-text-search-config": "pg_catalog.test"})
        harness.charm.state.__dict__.pop("config", None)

        with tc.assertRaises(ValueError) as e:
            harness.charm.config_manager.validate_config_options(harness.charm.postgresql)
            assert (
                e.msg == "instance-default-text-search-config config option has an invalid value"
            )

        _charm_lib.return_value.get_postgresql_text_search_configs.assert_called_once_with()
        _charm_lib.return_value.get_postgresql_text_search_configs.return_value = [
            "pg_catalog.test"
        ]

        # Test ldap-map exception
        with harness.hooks_disabled():
            harness.update_config({"ldap-map": "ldap_group="})
        harness.charm.state.__dict__.pop("config", None)
        harness.charm.state.__dict__.pop("config", None)

        with tc.assertRaises(ValueError) as e:
            harness.charm.config_manager.validate_config_options(harness.charm.postgresql)
            assert e.msg == "ldap-map config option has an invalid value"

        _charm_lib.return_value.validate_group_map.assert_called_once_with("ldap_group=")
        _charm_lib.return_value.validate_group_map.return_value = True

        # Test request-date-style exception
        with harness.hooks_disabled():
            harness.update_config({"request-date-style": "ISO, TEST"})
        harness.charm.state.__dict__.pop("config", None)

        with tc.assertRaises(ValueError) as e:
            harness.charm.config_manager.validate_config_options(harness.charm.postgresql)
            assert e.msg == "request-date-style config option has an invalid value"

        _charm_lib.return_value.validate_date_style.assert_called_once_with("ISO, TEST")
        _charm_lib.return_value.validate_date_style.return_value = ["ISO, TEST"]

        # Test request-time-zone exception
        with harness.hooks_disabled():
            harness.update_config({"request-time-zone": "TEST_ZONE"})
        harness.charm.state.__dict__.pop("config", None)

        with tc.assertRaises(ValueError) as e:
            harness.charm.config_manager.validate_config_options(harness.charm.postgresql)
            assert e.msg == "request-time-zone config option has an invalid value"

        _charm_lib.return_value.get_postgresql_timezones.assert_called_once_with()
        _charm_lib.return_value.get_postgresql_timezones.return_value = ["TEST_ZONE"]


def test_push_ca_file_into_workload(harness):
    with (
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
    ):
        harness.set_can_connect(POSTGRESQL_CONTAINER, True)
        harness.handle_exec(POSTGRESQL_CONTAINER, [], result=0)

        harness.charm.state.set_secret("unit", "ca-app", "test-ca")
        harness.charm.push_ca_file_into_workload("ca-app")
        _update_config.assert_called_once()

        container = harness.model.unit.get_container(POSTGRESQL_CONTAINER)
        ca_exists = container.exists(f"{CERTS_PATH}/ca-app.crt")
        assert ca_exists is True


def test_clean_ca_file_from_workload(harness):
    with (
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
    ):
        harness.set_can_connect(POSTGRESQL_CONTAINER, True)
        harness.handle_exec(POSTGRESQL_CONTAINER, [], result=0)

        harness.charm.state.set_secret("unit", "ca-app", "test-ca")
        harness.charm.push_ca_file_into_workload("ca-app")
        _update_config.reset_mock()

        harness.charm.clean_ca_file_from_workload("ca-app")
        _update_config.assert_called_once()

        container = harness.model.unit.get_container(POSTGRESQL_CONTAINER)
        ca_exists = container.exists(f"{CERTS_PATH}/ca-app.crt")
        assert ca_exists is False


def test_request_restart(harness):
    """The request_restart bridge refreshes the metrics scrape job and acquires the restart lock."""
    lock = Mock()
    harness.charm.restart_lock_acquire = lock
    with (
        patch(
            "single_kernel_postgresql.charms.k8s_charm.PostgreSQLK8sCharm._generate_metrics_jobs"
        ) as _generate_metrics_jobs,
        patch(
            "single_kernel_postgresql.managers.tls.TLSManager.get_client_tls_files",
            return_value=("k", "ca", "c"),
        ),
        patch("charm.MetricsEndpointProvider.update_scrape_job_spec") as _update_scrape_job_spec,
    ):
        harness.charm.request_restart()

        _update_scrape_job_spec.assert_called_once()
        lock.assert_called_once()


def test_restart_services(harness):
    """The restart_services bridge restarts both the metrics and LDAP sync pebble services."""
    with (
        patch(
            "charm.PostgresqlOperatorCharm._restart_metrics_service"
        ) as _restart_metrics_service,
        patch(
            "charm.PostgresqlOperatorCharm._restart_ldap_sync_service"
        ) as _restart_ldap_sync_service,
    ):
        harness.charm.restart_services()

        _restart_metrics_service.assert_called_once_with()
        _restart_ldap_sync_service.assert_called_once_with()


def test_create_pgdata(harness):
    container = MagicMock()
    container.exists.return_value = False
    container.list_files.return_value = []
    workload = harness.charm.workload
    workload.container = container
    workload.init_storage()
    # When directories don't exist, all required directories should be created.
    container.make_dir.assert_has_calls([
        call(
            "/var/lib/pg/data/16/main",
            permissions=448,
            user="postgres",
            group="postgres",
            make_parents=True,
        ),
        call(
            "/var/lib/pg/logs/16/main/pg_wal",
            permissions=448,
            user="postgres",
            group="postgres",
            make_parents=True,
        ),
        call(
            "/var/lib/pg/logs/16/main/pg_logs",
            permissions=493,
            user="postgres",
            group="postgres",
            make_parents=True,
        ),
        call(
            "/var/lib/pg/logs/16/main/patroni_logs",
            permissions=493,
            user="postgres",
            group="postgres",
            make_parents=True,
        ),
        call(
            "/var/lib/pg/logs/16/main/pgbackrest_logs",
            permissions=493,
            user="postgres",
            group="postgres",
            make_parents=True,
        ),
        call(
            "/var/lib/pg/temp/16/main/pgsql_tmp",
            permissions=448,
            user="postgres",
            group="postgres",
            make_parents=True,
        ),
        call(
            "/var/lib/pg/archive/16/main",
            permissions=448,
            user="postgres",
            group="postgres",
            make_parents=True,
        ),
    ])
    container.exec.assert_has_calls(
        [
            call(["chmod", "755", "/var/lib/pg/logs/16/main/pg_logs"]),
            call(["chown", "postgres:postgres", "/var/lib/pg/logs/16/main/pg_logs"]),
            call(["chmod", "755", "/var/lib/pg/logs/16/main/patroni_logs"]),
            call(["chown", "postgres:postgres", "/var/lib/pg/logs/16/main/patroni_logs"]),
            call(["chmod", "755", "/var/lib/pg/logs/16/main/pgbackrest_logs"]),
            call(["chown", "postgres:postgres", "/var/lib/pg/logs/16/main/pgbackrest_logs"]),
            call(["ln", "-sfn", "/var/lib/pg/data/16", "/var/lib/postgresql/16"]),
            call(["ln", "-sfn", "/var/lib/pg/logs/16/main/pg_logs", "/var/log/postgresql"]),
            call(["chown", "-h", "postgres:postgres", "/var/log/postgresql"]),
            call(["ln", "-sfn", "/var/lib/pg/logs/16/main/patroni_logs", "/var/log/patroni"]),
            call(["chown", "-h", "postgres:postgres", "/var/log/patroni"]),
            call([
                "ln",
                "-sfn",
                "/var/lib/pg/logs/16/main/pgbackrest_logs",
                "/var/log/pgbackrest",
            ]),
            call(["chown", "-h", "postgres:postgres", "/var/log/pgbackrest"]),
        ],
        any_order=True,
    )

    container.make_dir.reset_mock()
    container.exec.reset_mock()
    container.list_files.reset_mock()
    container.exists.return_value = True
    container.list_files.return_value = [MagicMock(type=FileType.SYMLINK)]
    workload = harness.charm.workload
    workload.container = container
    workload.init_storage()
    # When directories exist, none should be created
    container.make_dir.assert_not_called()
    container.list_files.assert_any_call("/var/log", pattern="postgresql")
    container.list_files.assert_any_call("/var/log", pattern="patroni")
    container.list_files.assert_any_call("/var/log", pattern="pgbackrest")
    container.remove_path.assert_not_called()
    container.exec.assert_has_calls(
        [
            call(["ln", "-sfn", "/var/lib/pg/data/16", "/var/lib/postgresql/16"]),
            call(["ln", "-sfn", "/var/lib/pg/logs/16/main/pg_logs", "/var/log/postgresql"]),
            call(["ln", "-sfn", "/var/lib/pg/logs/16/main/patroni_logs", "/var/log/patroni"]),
            call([
                "ln",
                "-sfn",
                "/var/lib/pg/logs/16/main/pgbackrest_logs",
                "/var/log/pgbackrest",
            ]),
        ],
        any_order=True,
    )


def test_create_pgdata_replaces_existing_directory_with_symlink(harness):
    # When /var/log/postgresql is a plain directory (not a symlink), it should be
    # removed and replaced with a symlink pointing to the new log location.
    container = MagicMock()
    container.exists.return_value = True
    container.exec.return_value.wait_output.return_value = ("755:postgres:postgres", "")

    def _list_files(path, *, pattern=None, itself=False):
        assert not itself
        if path == "/var/log":
            if pattern == "postgresql":
                return [MagicMock(type=FileType.DIRECTORY)]
            # patroni and pgbackrest paths are already symlinks — no replacement needed
            return [MagicMock(type=FileType.SYMLINK)]
        # list_files(symlink_path) is called to check whether the directory is empty
        assert path == "/var/log/postgresql"
        assert pattern is None
        return []

    container.list_files.side_effect = _list_files
    workload = harness.charm.workload
    workload.container = container

    workload.init_storage()

    container.list_files.assert_any_call("/var/log", pattern="postgresql")
    container.list_files.assert_any_call("/var/log/postgresql")
    container.remove_path.assert_called_once_with("/var/log/postgresql")
    assert call(["ln", "-sfn", "/var/lib/pg/logs/16/main/pg_logs", "/var/log/postgresql"]) in (
        container.exec.call_args_list
    )


def test_create_pgdata_raises_for_existing_non_directory(harness):
    container = MagicMock()
    container.exists.return_value = True
    container.exec.return_value.wait_output.return_value = ("755:postgres:postgres", "")
    container.list_files.return_value = [MagicMock(type=FileType.FILE)]
    workload = harness.charm.workload
    workload.container = container

    with pytest.raises(RuntimeError):
        workload.init_storage()

    container.list_files.assert_called_once_with("/var/log", pattern="postgresql")
    container.remove_path.assert_not_called()
    assert call(["ln", "-sfn", "/var/lib/pg/logs/16/main/pg_logs", "/var/log/postgresql"]) not in (
        container.exec.call_args_list
    )


@pytest.mark.parametrize(
    "symlink_path,target",
    [
        ("/var/log/patroni", "/var/lib/pg/logs/16/main/patroni_logs"),
        ("/var/log/pgbackrest", "/var/lib/pg/logs/16/main/pgbackrest_logs"),
    ],
)
def test_create_pgdata_replaces_existing_directory_for_extra_log_symlinks(
    harness, symlink_path, target
):
    """Existing empty directories at patroni/pgbackrest symlink paths are replaced."""
    container = MagicMock()
    container.exists.return_value = True
    container.exec.return_value.wait_output.return_value = ("755:postgres:postgres", "")
    parent = "/var/log"
    name = symlink_path.rsplit("/", 1)[-1]

    def _list_files(path, *, pattern=None, itself=False):
        assert not itself
        if path == parent:
            if pattern == name:
                return [MagicMock(type=FileType.DIRECTORY)]
            return [MagicMock(type=FileType.SYMLINK)]
        # empty-directory check
        if path == symlink_path:
            assert pattern is None
            return []
        return [MagicMock(type=FileType.SYMLINK)]

    container.list_files.side_effect = _list_files
    workload = harness.charm.workload
    workload.container = container

    workload.init_storage()

    container.remove_path.assert_called_once_with(symlink_path)
    assert call(["ln", "-sfn", target, symlink_path]) in container.exec.call_args_list


@pytest.mark.parametrize(
    "symlink_path",
    ["/var/log/patroni", "/var/log/pgbackrest"],
)
def test_create_pgdata_raises_for_existing_non_directory_extra_log_symlinks(harness, symlink_path):
    """A non-symlink, non-directory file at patroni/pgbackrest paths causes RuntimeError."""
    container = MagicMock()
    container.exists.return_value = True
    container.exec.return_value.wait_output.return_value = ("755:postgres:postgres", "")
    parent = "/var/log"
    name = symlink_path.rsplit("/", 1)[-1]

    def _list_files(path, *, pattern=None, itself=False):
        assert not itself
        if path == parent:
            if pattern == name:
                return [MagicMock(type=FileType.FILE)]
            return [MagicMock(type=FileType.SYMLINK)]
        return []

    container.list_files.side_effect = _list_files
    workload = harness.charm.workload
    workload.container = container

    with pytest.raises(RuntimeError):
        workload.init_storage()

    container.remove_path.assert_not_called()

    with patch("single_kernel_postgresql.charms.k8s_charm.PostgreSQLK8sCharm._on_config_changed"):
        # Test when the charm has no plugins enabled.
        assert harness.charm.config_manager.get_plugins() == ["pgaudit"]

        # Test when the charm has some plugins enabled.
        harness.update_config({
            "plugin-audit-enable": True,
            "plugin-citext-enable": True,
            "plugin-spi-enable": True,
        })
        harness.charm.state.__dict__.pop("config", None)
        assert harness.charm.config_manager.get_plugins() == [
            "pgaudit",
            "citext",
            "refint",
            "autoinc",
            "insert_username",
            "moddatetime",
        ]

        # Test when the charm has the pgAudit plugin disabled.
        harness.update_config({"plugin-audit-enable": False})
        harness.charm.state.__dict__.pop("config", None)
        assert harness.charm.config_manager.get_plugins() == [
            "citext",
            "refint",
            "autoinc",
            "insert_username",
            "moddatetime",
        ]


def test_get_ldap_parameters(harness):
    with (
        patch("single_kernel_postgresql.events.ldap.LDAP.get_relation_data") as _get_relation_data,
        patch(
            target="single_kernel_postgresql.core.peer_relation.PostgreSQLApplication.is_cluster_initialised",
            new_callable=PropertyMock,
            return_value=True,
        ) as _cluster_initialised,
    ):
        with harness.hooks_disabled():
            harness.update_relation_data(
                harness.model.get_relation(PEER_RELATION).id,
                harness.charm.app.name,
                {"ldap_enabled": "False"},
            )

        harness.charm.ldap.get_ldap_parameters()
        _get_relation_data.assert_not_called()
        _get_relation_data.reset_mock()

        with harness.hooks_disabled():
            harness.update_relation_data(
                harness.model.get_relation(PEER_RELATION).id,
                harness.charm.app.name,
                {"ldap_enabled": "True"},
            )

        harness.charm.ldap.get_ldap_parameters()
        _get_relation_data.assert_called_once()
        _get_relation_data.reset_mock()


def test_on_secret_remove(harness):
    with (
        patch("ops.model.Model.juju_version", new_callable=PropertyMock) as _juju_version,
    ):
        event = Mock()

        # New juju
        _juju_version.return_value = JujuVersion("3.6.11")
        harness.charm.postgresql_events_handler._on_secret_remove(event)
        event.remove_revision.assert_called_once_with()
        event.reset_mock()

        # Old juju
        _juju_version.return_value = JujuVersion("3.6.9")
        harness.charm.postgresql_events_handler._on_secret_remove(event)
        assert not event.remove_revision.called
        event.reset_mock()

        # No secret
        event.secret.label = None
        harness.charm.postgresql_events_handler._on_secret_remove(event)
        assert not event.remove_revision.called


def test_planned_units_returns_app_value_normally(harness):
    charm = harness.charm
    with patch.object(charm.app, "planned_units", return_value=5):
        assert charm.state.application.planned_units == 5


def test_planned_units_survives_goal_state_failure(harness):
    # DPE-10203 Issue B: after a cross-model SAAS is force-removed, the goal-state hook
    # command fails ("saas application ... not found"), so app.planned_units() raises
    # ModelError. planned_units must fall back to the current unit count instead of
    # crashing every hook that reads it.
    charm = harness.charm
    with patch.object(
        charm.app,
        "planned_units",
        side_effect=ModelError('ERROR saas application "db1" not found'),
    ):
        assert charm.state.application.planned_units == len(charm.membership_manager.hosts())
