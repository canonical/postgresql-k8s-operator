# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import mock_open, patch, sentinel

from scripts.authorisation_rules_observer import check_for_database_changes


def test_check_for_database_changes():
    with (
        patch("scripts.authorisation_rules_observer.subprocess") as _subprocess,
        patch("scripts.authorisation_rules_observer.psycopg2") as _psycopg2,
    ):
        run_cmd = "run_cmd"
        unit = "unit/0"
        charm_dir = "charm_dir"
        mock = mock_open(
            read_data="""postgresql:
  authentication:
    superuser:
      username: test_user
      password: test_password"""
        )
        with patch("builtins.open", mock, create=True):
            _cursor = _psycopg2.connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            _cursor.fetchall.return_value = sentinel.databases

            # Test the first time this function is called.
            result = check_for_database_changes(run_cmd, unit, charm_dir, None)
            assert result == sentinel.databases
            _subprocess.run.assert_not_called()
            _psycopg2.connect.assert_called_once_with(
                "dbname='postgres' user='operator' host='localhost'password='test_password' connect_timeout=1"
            )
            _cursor.execute.assert_called_once_with("SELECT datname,datacl FROM pg_database;")

            # Test when the databases changed.
            _cursor.fetchall.return_value = sentinel.databases_changed
            result = check_for_database_changes(run_cmd, unit, charm_dir, result)
            assert result == sentinel.databases_changed

            _subprocess.run.assert_called_once_with([
                run_cmd,
                "-u",
                unit,
                f"JUJU_DISPATCH_PATH=hooks/databases_change {charm_dir}/dispatch",
            ])

            # Test when the databases haven't changed.
            _subprocess.reset_mock()
            check_for_database_changes(run_cmd, unit, charm_dir, result)
            assert result == sentinel.databases_changed
            _subprocess.run.assert_not_called()
