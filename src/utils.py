# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""A collection of utility functions that are used in the charm."""

import re
import secrets
import string


def new_password() -> str:
    """Generate a random password string.

    Returns:
       A random password string.
    """
    choices = string.ascii_letters + string.digits
    password = "".join([secrets.choice(choices) for i in range(16)])
    return password


def split_mem(mem_str) -> tuple:
    """Split a memory string into a number and a unit.

    Args:
        mem_str: a string representing a memory value, e.g. "1Gi"
    """
    pattern = r"^(\d+)(\w+)$"
    parts = re.match(pattern, mem_str)
    if parts:
        return parts.groups()
    return None, "No unit found"


def any_memory_to_bytes(mem_str) -> int:
    """Convert a memory string to bytes.

    Args:
        mem_str: a string representing a memory value, e.g. "1Gi"
    """
    units = {
        "KI": 1024,
        "K": 10**3,
        "MI": 1048576,
        "M": 10**6,
        "GI": 1073741824,
        "G": 10**9,
        "TI": 1099511627776,
        "T": 10**12,
    }
    try:
        num = int(mem_str)
        return num
    except ValueError as e:
        memory, unit = split_mem(mem_str)
        unit = unit.upper()
        if unit not in units:
            raise ValueError(f"Invalid memory definition in '{mem_str}'") from e

        num = int(memory)
        return int(num * units[unit])


def any_cpu_to_cores(cpu_str) -> int:
    """Convert a CPU string to cores.

    Args:
        cpu_str: a string representing a CPU value, as integer or millis
    """
    if cpu_str.endswith("m"):
        # convert millis to cores, undercommited
        return int(cpu_str[:-1]) // 1000
    return int(cpu_str)


def label2name(label: str) -> str:
    """Convert a unit label (with `-`) to a unit name (with `/`).

    Args:
        label: The label to convert.

    Returns:
        The converted name.
    """
    return label.rsplit("-", 1)[0] + "/" + label.rsplit("-", 1)[1]
