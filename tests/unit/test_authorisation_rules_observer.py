# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import sys
from unittest.mock import Mock, mock_open, patch, sentinel

import pytest

from scripts.authorisation_rules_observer import (
    UnreachableUnitsError,
    check_for_database_changes,
    main,
)


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
                "dbname='postgres' user='operator' host='localhost' password='test_password' connect_timeout=1"
            )
            _cursor.execute.assert_called_once_with("SELECT datname, datacl FROM pg_database;")

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


async def test_main():
    with (
        patch("scripts.authorisation_rules_observer.check_for_database_changes"),
        patch.object(
            sys,
            "argv",
            ["cmd", "http://server1:8008,http://server2:8008", "run_cmd", "unit/0", "charm_dir"],
        ),
        patch("scripts.authorisation_rules_observer.sleep", return_value=None),
        patch("scripts.authorisation_rules_observer.AsyncClient") as _async_client,
        patch("scripts.authorisation_rules_observer.create_default_context") as _context,
    ):
        mock1 = Mock()
        mock1.json.return_value = {
            "members": [
                {"name": "unit-2", "api_url": "http://server3:8008/patroni", "role": "standby"},
                {"name": "unit-0", "api_url": "http://server1:8008/patroni", "role": "leader"},
            ]
        }
        mock2 = Mock()
        mock2.json.return_value = {
            "members": [
                {"name": "unit-2", "api_url": "https://server3:8008/patroni", "role": "leader"},
            ]
        }
        async with _async_client() as cli:
            _get = cli.get
            _get.side_effect = [
                mock1,
                Exception,
                mock2,
            ]
        with pytest.raises(UnreachableUnitsError):
            await main()
        _async_client.assert_any_call(timeout=5, verify=_context.return_value)
        _get.assert_any_call("http://server1:8008/cluster")
        _get.assert_any_call("http://server3:8008/cluster")
