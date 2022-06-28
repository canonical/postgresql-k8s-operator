#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
from typing import List

from lightkube import codecs
from lightkube.core.client import Client
from lightkube.core.exceptions import ApiError
from lightkube.resources.core_v1 import Endpoints, Service
from lightkube.resources.rbac_authorization_v1 import ClusterRole, ClusterRoleBinding
from pytest_operator.plugin import OpsTest


def get_charm_resources(namespace: str, application: str):
    context = {"namespace": namespace, "app_name": application}

    # Check if any resource created by the charm still exists.
    with open("src/resources.yaml") as f:
        # Load the list of the resources that should be created by the charm.
        charm_resources = list(
            filter(
                lambda x: not isinstance(x, (ClusterRole, ClusterRoleBinding, Service)),
                codecs.load_all_yaml(f, context=context),
            )
        )
        return charm_resources


def get_expected_patroni_k8s_resources(application: str, namespace: str) -> set[str]:
    resources = set()

    # Define the context needed for the k8s resources lists load.
    context = {"namespace": namespace, "app_name": application}

    with open("src/resources.yaml") as f:
        # Load the list of the resources that should be created by the charm.
        charm_resources = codecs.load_all_yaml(f, context=context)
        resources = set(
            map(
                lambda x: f"{type(x).__name__}/{x.metadata.name}",
                charm_resources,
            )
        )

    # Include the resources that Patroni creates when it starts.
    patroni_resources = [
        f'Endpoints/{namespace}-config', f'Endpoints/{namespace}', f'Service/{namespace}-config'
    ]
    resources.update(patroni_resources)

    return resources


def get_existing_patroni_k8s_resources(
    ops_test: OpsTest, application: str, namespace: str
) -> set[str]:
    """Count the k8s resources that were created by the charm or by Patroni.

    Args:
        ops_test: ops_test instance.
        application: application name.
        namespace: namespace related to the model where
            the charm was deployed.

    Returns:
        count of existing charm/Patroni specific k8s resources.
    """
    resources = set()

    # Define the context needed for the k8s resources lists load.
    client = Client(namespace=namespace)

    # Define the context needed for the k8s resources lists load.
    context = {"namespace": namespace, "app_name": application}

    with open("src/resources.yaml") as f:
        # Load the list of the resources that should be created by the charm.
        charm_resources = codecs.load_all_yaml(f, context=context)

    existing_charm_resources = list(
        map(
            lambda x: f"{type(x).__name__}/{x.metadata.name}",
            filter(
                lambda x: (resource_exists(client, x)),
                charm_resources,
            )
        )
    )
    # Add only the existing resources to the list.
    resources.update(
        set(
            map(
                lambda x: f"{x.split('/')[0]}/{x.split('/')[1]}",
                existing_charm_resources,
            )
        )
    )

    # List the resources created by Patroni.
    for kind in [Endpoints, Service]:
        patroni_resources = client.list(
            kind,
            namespace=namespace,
            labels={"app.juju.is/created-by": application},
        )
        mapped_patroni_resources = set(
                map(
                    lambda x: f"{kind.__name__}/{x.metadata.name}",
                    patroni_resources,
                )
            )
        resources.update(
            mapped_patroni_resources
        )

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


def resource_exists(client: Client, resource) -> bool:
    try:
        client.get(type(resource), name=resource.metadata.name)
        return True
    except ApiError as e:
        return False
