# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import re

from utils import any_cpu_to_cores, any_memory_to_bytes, new_password


def test_new_password():
    # Test the password generation twice in order to check if we get different passwords and
    # that they meet the required criteria.
    first_password = new_password()
    assert len(first_password) == 16
    assert re.fullmatch("[a-zA-Z0-9\b]{16}$", first_password) is not None

    second_password = new_password()
    assert re.fullmatch("[a-zA-Z0-9\b]{16}$", second_password) is not None
    assert second_password != first_password


def test_any_memory_to_bytes():
    assert any_memory_to_bytes("1KI") == 1024

    try:
        any_memory_to_bytes("KI")
        assert False
    except ValueError as e:
        assert str(e) == "Invalid memory definition in 'KI'"


def test_any_cpu_to_cores():
    assert any_cpu_to_cores("12") == 12
    assert any_cpu_to_cores("1000m") == 1
