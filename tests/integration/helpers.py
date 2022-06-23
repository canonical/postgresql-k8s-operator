#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
from typing import List

import requests
from pytest_operator.plugin import OpsTest

TLS_RESOURCES = {
    "cert-file": "tests/tls/server.crt",
    "key-file": "tests/tls/server.key",
}


async def attach_resource(ops_test: OpsTest, app_name: str, rsc_name: str, rsc_path: str) -> None:
    """Use the `juju attach-resource` command to add resources."""
    # logger.info(f"Attaching resource: attach-resource {APP_NAME} {rsc_name}={rsc_path}")
    await ops_test.juju("attach-resource", app_name, f"{rsc_name}={rsc_path}")


def convert_records_to_dict(records: List[tuple]) -> dict:
    """Converts psycopg2 records list to a dict."""
    records_dict = {}
    for record in records:
        # Add record tuple data to dict.
        records_dict[record[0]] = record[1]
    return records_dict


def get_cluster_members(endpoint: str) -> List[str]:
    """List of current Patroni cluster members.

    Args:
        endpoint: endpoint of the Patroni API

    Returns:
        list of Patroni cluster members
    """
    r = requests.get(f"http://{endpoint}:8008/cluster")
    return [member["name"] for member in r.json()["members"]]


def get_application_units(ops_test: OpsTest, application_name: str) -> List[str]:
    """List the unit names of an application.

    Args:
        ops_test: The ops test framework instance
        application_name: The name of the application

    Returns:
        list of current unit names of the application
    """
    return [
        unit.name.replace("/", "-") for unit in ops_test.model.applications[application_name].units
    ]


async def get_unit_address(ops_test: OpsTest, application_name: str, unit_name: str) -> str:
    """Get unit IP address.

    Args:
        ops_test: The ops test framework instance
        application_name: The name of the application
        unit_name: The name of the unit

    Returns:
        IP address of the unit
    """
    status = await ops_test.model.get_status()
    return status["applications"][application_name].units[unit_name]["address"]


async def scale_application(ops_test: OpsTest, application_name: str, scale: int) -> None:
    """Scale a given application to a specific unit count.

    Args:
        ops_test: The ops test framework instance
        application_name: The name of the application
        scale: The number of units to scale to
    """
    await ops_test.model.applications[application_name].scale(scale)
    await ops_test.model.wait_for_idle(apps=[application_name], status="active", timeout=1000)
