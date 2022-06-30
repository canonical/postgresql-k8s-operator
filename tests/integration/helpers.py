#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

from typing import List

import requests
from lightkube import codecs
from lightkube.core.client import Client
from lightkube.core.exceptions import ApiError
from lightkube.core.resource import NamespacedResourceG
from lightkube.resources.core_v1 import Endpoints, Service
from lightkube.resources.rbac_authorization_v1 import ClusterRole, ClusterRoleBinding
from pytest_operator.plugin import OpsTest


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


def get_charm_resources(namespace: str, application: str):
    """Return the list of k8s resources from resources.yaml file.

    Args:
        namespace: namespace related to the model where
            the charm was deployed.
        application: application name.

    Returns:
        list of existing charm/Patroni specific k8s resources.
    """
    # Define the context needed for the k8s resources lists load.
    context = {"namespace": namespace, "app_name": application}

    # Load the list of the resources from resources.yaml.
    with open("src/resources.yaml") as f:
        return list(
            filter(
                lambda x: not isinstance(x, (ClusterRole, ClusterRoleBinding, Service)),
                codecs.load_all_yaml(f, context=context),
            )
        )


def get_existing_patroni_k8s_resources(namespace: str, application: str) -> set[str]:
    """Return the list of k8s resources that were created by the charm and Patroni.

    Args:
        namespace: namespace related to the model where
            the charm was deployed.
        application: application name.

    Returns:
        list of existing charm/Patroni specific k8s resources.
    """
    # Create a k8s API client instance.
    client = Client(namespace=namespace)

    # Retrieve the k8s resources the charm should create.
    charm_resources = get_charm_resources(namespace, application)

    # Check the k8s API for the resources that currently exist.
    existing_charm_resources = list(
        map(
            lambda x: f"{type(x).__name__}/{x.metadata.name}",
            filter(
                lambda x: (resource_exists(client, x)),
                charm_resources,
            ),
        )
    )

    # Add only the existing resources to the list.
    resources = set(
        map(
            lambda x: f"{x.split('/')[0]}/{x.split('/')[1]}",
            existing_charm_resources,
        )
    )

    # Include the resources created by Patroni.
    for kind in [Endpoints, Service]:
        patroni_resources = client.list(
            kind,
            namespace=namespace,
            labels={"app.juju.is/created-by": application},
        )

        # Build an identifier for each resource (using its type and name).
        mapped_patroni_resources = set(
            map(
                lambda x: f"{kind.__name__}/{x.metadata.name}",
                patroni_resources,
            )
        )

        resources.update(mapped_patroni_resources)

    return resources


def get_expected_patroni_k8s_resources(namespace: str, application: str) -> set[str]:
    """Return the list of expected k8s resources when the charm is deployed.

    Args:
        namespace: namespace related to the model where
            the charm was deployed.
        application: application name.

    Returns:
        list of existing charm/Patroni specific k8s resources.
    """
    # Retrieve the k8s resources created by the charm.
    charm_resources = get_charm_resources(namespace, application)

    # Build an identifier for each resource (using its type and name).
    resources = set(
        map(
            lambda x: f"{type(x).__name__}/{x.metadata.name}",
            charm_resources,
        )
    )

    # Include the resources created by Patroni.
    patroni_resources = [
        f"Endpoints/{namespace}-config",
        f"Endpoints/{namespace}",
        f"Service/{namespace}-config",
    ]
    resources.update(patroni_resources)

    return resources


async def get_model_name(ops_test: OpsTest) -> str:
    """Get the name of the current model.

    Args:
        ops_test: ops_test instance.

    Returns:
        model name.
    """
    model = await ops_test.model.get_info()
    return model.name


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


def resource_exists(client: Client, resource: NamespacedResourceG) -> bool:
    """Get the name of the current model.

    Args:
        client: k8s API client instance.
        resource: k8s resource.

    Returns:
        whether the resource exists.
    """
    try:
        client.get(type(resource), name=resource.metadata.name)
        return True
    except ApiError:
        return False


async def scale_application(ops_test: OpsTest, application_name: str, scale: int) -> None:
    """Scale a given application to a specific unit count.

    Args:
        ops_test: The ops test framework instance
        application_name: The name of the application
        scale: The number of units to scale to
    """
    await ops_test.model.applications[application_name].scale(scale)
    await ops_test.model.wait_for_idle(apps=[application_name], status="active", timeout=1000)
