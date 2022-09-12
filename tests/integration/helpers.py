#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import itertools
from datetime import datetime
from pathlib import Path
from typing import List

import psycopg2
import requests
import yaml
from lightkube import codecs
from lightkube.core.client import Client
from lightkube.core.exceptions import ApiError
from lightkube.generic_resource import GenericNamespacedResource
from lightkube.resources.core_v1 import Endpoints, Service
from pytest_operator.plugin import OpsTest
from tenacity import (
    RetryError,
    Retrying,
    retry,
    retry_if_result,
    stop_after_attempt,
    wait_exponential,
)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
DATABASE_APP_NAME = METADATA["name"]


async def check_database_users_existence(
    ops_test: OpsTest,
    users_that_should_exist: List[str],
    users_that_should_not_exist: List[str],
    admin: bool = False,
) -> None:
    """Checks that applications users exist in the database.

    Args:
        ops_test: The ops test framework
        users_that_should_exist: List of users that should exist in the database
        users_that_should_not_exist: List of users that should not exist in the database
        admin: Whether to check if the existing users are superusers
    """
    unit = ops_test.model.applications[DATABASE_APP_NAME].units[0]
    unit_address = await get_unit_address(ops_test, unit.name)
    password = await get_password(ops_test)

    # Retrieve all users in the database.
    output = await execute_query_on_unit(
        unit_address,
        password,
        "SELECT CONCAT(usename, ':', usesuper) FROM pg_catalog.pg_user;"
        if admin
        else "SELECT usename FROM pg_catalog.pg_user;",
    )

    # Assert users that should exist.
    for user in users_that_should_exist:
        if admin:
            # The t flag indicates the user is a superuser.
            assert f"{user}:t" in output
        else:
            assert user in output

    # Assert users that should not exist.
    for user in users_that_should_not_exist:
        assert user not in output


async def check_database_creation(ops_test: OpsTest, database: str) -> None:
    """Checks that database and tables are successfully created for the application.

    Args:
        ops_test: The ops test framework
        database: Name of the database that should have been created
    """
    password = await get_password(ops_test)

    for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
        unit_address = await get_unit_address(ops_test, unit.name)

        # Ensure database exists in PostgreSQL.
        output = await execute_query_on_unit(
            unit_address,
            password,
            "SELECT datname FROM pg_database;",
        )
        assert database in output

        # Ensure that application tables exist in the database
        output = await execute_query_on_unit(
            unit_address,
            password,
            "SELECT table_name FROM information_schema.tables;",
            database=database,
        )
        assert len(output)


@retry(
    retry=retry_if_result(lambda x: not x),
    stop=stop_after_attempt(10),
    wait=wait_exponential(multiplier=1, min=2, max=30),
)
async def check_patroni(ops_test: OpsTest, unit_name: str, restart_time: float) -> bool:
    """Check if Patroni is running correctly on a specific unit.

    Args:
        ops_test: The ops test framework instance
        unit_name: The name of the unit
        restart_time: Point in time before the unit was restarted.

    Returns:
        whether Patroni is running correctly.
    """
    unit_ip = await get_unit_address(ops_test, unit_name)
    health_info = requests.get(f"http://{unit_ip}:8008/health").json()
    postmaster_start_time = datetime.strptime(
        health_info["postmaster_start_time"], "%Y-%m-%d %H:%M:%S.%f%z"
    ).timestamp()
    return postmaster_start_time > restart_time and health_info["state"] == "running"


def convert_records_to_dict(records: List[tuple]) -> dict:
    """Converts psycopg2 records list to a dict."""
    records_dict = {}
    for record in records:
        # Add record tuple data to dict.
        records_dict[record[0]] = record[1]
    return records_dict


async def deploy_and_relate_application_with_postgresql(
    ops_test: OpsTest,
    charm: str,
    application_name: str,
    number_of_units: int,
    channel: str = "stable",
    relation: str = "db",
    status: str = "blocked",
) -> int:
    """Helper function to deploy and relate application with PostgreSQL.

    Args:
        ops_test: The ops test framework.
        charm: Charm identifier.
        application_name: The name of the application to deploy.
        number_of_units: The number of units to deploy.
        channel: The channel to use for the charm.
        relation: Name of the PostgreSQL relation to relate
            the application to.
        status: The status to wait for in the application (default: blocked).

    Returns:
        the id of the created relation.
    """
    # Deploy application.
    await ops_test.model.deploy(
        charm,
        channel=channel,
        application_name=application_name,
        num_units=number_of_units,
    )
    await ops_test.model.wait_for_idle(
        apps=[application_name],
        status=status,
        raise_on_blocked=False,
        timeout=1000,
    )

    # Relate application to PostgreSQL.
    relation = await ops_test.model.relate(
        f"{application_name}", f"{DATABASE_APP_NAME}:{relation}"
    )
    await ops_test.model.wait_for_idle(
        apps=[application_name],
        status="active",
        raise_on_blocked=False,  # Application that needs a relation is blocked initially.
        timeout=1000,
    )

    return relation.id


async def execute_query_on_unit(
    unit_address: str,
    password: str,
    query: str,
    database: str = "postgres",
    sslmode: str = None,
):
    """Execute given PostgreSQL query on a unit.

    Args:
        unit_address: The public IP address of the unit to execute the query on.
        password: The PostgreSQL superuser password.
        query: Query to execute.
        database: Optional database to connect to (defaults to postgres database).
        sslmode: Optional ssl mode to use (defaults to None).

    Returns:
        The result of the query.
    """
    extra_connection_parameters = f" sslmode={sslmode}" if sslmode is not None else ""
    with psycopg2.connect(
        f"dbname='{database}' user='operator' host='{unit_address}'"
        f"password='{password}' connect_timeout=10{extra_connection_parameters}"
    ) as connection, connection.cursor() as cursor:
        cursor.execute(query)
        output = list(itertools.chain(*cursor.fetchall()))
    return output


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


def get_charm_resources(namespace: str, application: str) -> List[GenericNamespacedResource]:
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
        return codecs.load_all_yaml(f, context=context)


def get_existing_k8s_resources(namespace: str, application: str) -> set:
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

    # Add only the resources that currently exist.
    resources = set(
        map(
            # Build an identifier for each resource (using its type and name).
            lambda x: f"{type(x).__name__}/{x.metadata.name}",
            filter(
                lambda x: (resource_exists(client, x)),
                charm_resources,
            ),
        )
    )

    # Include the resources created by the charm and Patroni.
    for kind in [Endpoints, Service]:
        extra_resources = client.list(
            kind,
            namespace=namespace,
            labels={"app.juju.is/created-by": application},
        )
        resources.update(
            set(
                map(
                    # Build an identifier for each resource (using its type and name).
                    lambda x: f"{kind.__name__}/{x.metadata.name}",
                    extra_resources,
                )
            )
        )

    return resources


def get_expected_k8s_resources(namespace: str, application: str) -> set:
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

    # Include the resources created by the charm and Patroni.
    resources.update(
        [
            f"Endpoints/patroni-{application}-config",
            f"Endpoints/patroni-{application}",
            f"Endpoints/{application}-primary",
            f"Endpoints/{application}-replicas",
            f"Service/patroni-{application}-config",
        ]
    )

    return resources


async def get_password(ops_test: OpsTest, username: str = "operator"):
    """Retrieve a user password using the action."""
    unit = ops_test.model.units.get(f"{DATABASE_APP_NAME}/0")
    action = await unit.run_action("get-password", **{"username": username})
    result = await action.wait()
    return result.results[f"{username}-password"]


async def get_primary(ops_test: OpsTest, unit_id=0) -> str:
    """Get the primary unit.

    Args:
        ops_test: ops_test instance.
        unit_id: the number of the unit.

    Returns:
        the current primary unit.
    """
    action = await ops_test.model.units.get(f"{DATABASE_APP_NAME}/{unit_id}").run_action(
        "get-primary"
    )
    action = await action.wait()
    return action.results["primary"]


async def get_unit_address(ops_test: OpsTest, unit_name: str) -> str:
    """Get unit IP address.

    Args:
        ops_test: The ops test framework instance
        unit_name: The name of the unit

    Returns:
        IP address of the unit
    """
    status = await ops_test.model.get_status()
    return status["applications"][unit_name.split("/")[0]].units[unit_name]["address"]


async def check_tls(ops_test: OpsTest, unit_name: str, enabled: bool) -> bool:
    """Returns whether TLS is enabled on the specific PostgreSQL instance.

    Args:
        ops_test: The ops test framework instance.
        unit_name: The name of the unit of the PostgreSQL instance.
        enabled: check if TLS is enabled/disabled

    Returns:
        Whether TLS is enabled/disabled.
    """
    unit_address = await get_unit_address(ops_test, unit_name)
    password = await get_password(ops_test)
    try:
        for attempt in Retrying(
            stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
        ):
            with attempt:
                output = await execute_query_on_unit(
                    unit_address,
                    password,
                    "SHOW ssl;",
                    sslmode="require" if enabled else "disable",
                )
                tls_enabled = "on" in output
                if enabled != tls_enabled:
                    raise ValueError(
                        f"TLS is{' not' if not tls_enabled else ''} enabled on {unit_name}"
                    )
                return True
    except RetryError:
        return False


async def restart_patroni(ops_test: OpsTest, unit_name: str) -> None:
    """Restart Patroni on a specific unit.

    Args:
        ops_test: The ops test framework instance
        unit_name: The name of the unit
    """
    unit_ip = await get_unit_address(ops_test, unit_name)
    requests.post(f"http://{unit_ip}:8008/restart")


def resource_exists(client: Client, resource: GenericNamespacedResource) -> bool:
    """Check whether a specific resource exists.

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
    await ops_test.model.wait_for_idle(
        apps=[application_name],
        status="active",
        timeout=1000,
        wait_for_exact_units=scale,
    )


async def set_password(
    ops_test: OpsTest, unit_name: str, username: str = "operator", password: str = None
):
    """Set a user password using the action."""
    unit = ops_test.model.units.get(unit_name)
    parameters = {"username": username}
    if password is not None:
        parameters["password"] = password
    action = await unit.run_action("set-password", **parameters)
    result = await action.wait()
    return result.results
