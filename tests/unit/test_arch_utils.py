# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import builtins
import sys
import unittest.mock as mock
from unittest.mock import patch

import pytest

from arch_utils import is_wrong_architecture

real_import = builtins.__import__


def psycopg2_not_found(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
    """Fake import function to simulate psycopg2 import error."""
    if name == "psycopg2":
        raise ModuleNotFoundError(f"Mocked module not found {name}")
    return real_import(name, globals=globals, locals=locals, fromlist=fromlist, level=level)


def test_on_module_not_found_error(monkeypatch):
    """Checks if is_wrong_architecture is called on ModuleNotFoundError."""
    with patch("arch_utils.is_wrong_architecture") as _is_wrong_arch:
        # If psycopg2 not there, charm should check architecture
        monkeypatch.delitem(sys.modules, "psycopg2", raising=False)
        monkeypatch.delitem(sys.modules, "charm", raising=False)
        monkeypatch.delitem(sys.modules, "charm_refresh", raising=False)
        monkeypatch.setattr(builtins, "__import__", psycopg2_not_found)
        with pytest.raises(ModuleNotFoundError):
            import charm

        _is_wrong_arch.assert_called_once()

        # If no import errors, charm continues as normal
        _is_wrong_arch.reset_mock()
        monkeypatch.setattr(builtins, "__import__", real_import)
        import charm  # noqa: F401

        _is_wrong_arch.assert_not_called()


def test_wrong_architecture_file_not_found():
    """Tests if the function returns False when the charm file doesn't exist."""
    with (
        patch("os.environ.get", return_value="/tmp"),
        patch("os.path.exists", return_value=False),
    ):
        assert not is_wrong_architecture()


def test_wrong_architecture_amd64():
    """Tests if the function correctly identifies arch when charm is AMD."""
    with (
        patch("os.environ.get", return_value="/tmp"),
        patch("os.path.exists", return_value=True),
        patch("builtins.open", mock.mock_open(read_data="amd64\n")),
        patch("os.uname") as _uname,
    ):
        _uname.return_value = mock.Mock(machine="x86_64")
        assert not is_wrong_architecture()
        _uname.return_value = mock.Mock(machine="aarch64")
        assert is_wrong_architecture()


def test_wrong_architecture_arm64():
    """Tests if the function correctly identifies arch when charm is ARM."""
    with (
        patch("os.environ.get", return_value="/tmp"),
        patch("os.path.exists", return_value=True),
        patch("builtins.open", mock.mock_open(read_data="arm64\n")),
        patch("os.uname") as _uname,
    ):
        _uname.return_value = mock.Mock(machine="x86_64")
        assert is_wrong_architecture()
        _uname.return_value = mock.Mock(machine="aarch64")
        assert not is_wrong_architecture()
