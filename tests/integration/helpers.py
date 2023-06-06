#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio
import itertools
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import botocore
import psycopg2
import requests
import yaml
from lightkube.core.client import Client
from lightkube.core.exceptions import ApiError
from lightkube.generic_resource import GenericNamespacedResource
from lightkube.resources.core_v1 import Endpoints, Service
from pytest_operator.plugin import OpsTest
from tenacity import (
    RetryError,
    Retrying,
    retry,
    retry_if_exception,
    retry_if_result,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential,
    wait_fixed,
)

CHARM_SERIES = "jammy"
METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
DATABASE_APP_NAME = METADATA["name"]

charm = None


async def app_name(ops_test: OpsTest, application_name: str = "postgresql-k8s") -> Optional[str]:
    """Returns the name of the cluster running PostgreSQL.

    This is important since not all deployments of the PostgreSQL charm have the application name
    "postgresql-k8s".

    Note: if multiple clusters are running PostgreSQL this will return the one first found.
    """
    status = await ops_test.model.get_status()
    for app in ops_test.model.applications:
        if application_name in status["applications"][app]["charm"]:
            return app

    return None


async def build_and_deploy(
    ops_test: OpsTest,
    num_units: int,
    database_app_name: str = DATABASE_APP_NAME,
    wait_for_idle: bool = True,
    status: str = "active",
) -> None:
    """Builds the charm and deploys a specified number of units."""
    # It is possible for users to provide their own cluster for testing. Hence, check if there
    # is a pre-existing cluster.
    if await app_name(ops_test, database_app_name):
        return

    global charm
    if not charm:
        charm = await ops_test.build_charm(".")
    resources = {
        "postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"],
    }
    await ops_test.model.deploy(
        charm,
        resources=resources,
        application_name=database_app_name,
        trust=True,
        num_units=num_units,
        series=CHARM_SERIES,
    ),
    if wait_for_idle:
        # Wait until the PostgreSQL charm is successfully deployed.
        await ops_test.model.wait_for_idle(
            apps=[database_app_name],
            status=status,
            raise_on_blocked=True,
            timeout=1000,
            wait_for_exact_units=num_units,
        )


async def check_database_users_existence(
    ops_test: OpsTest,
    users_that_should_exist: List[str],
    users_that_should_not_exist: List[str],
    admin: bool = False,
    database_app_name: str = DATABASE_APP_NAME,
) -> None:
    """Checks that applications users exist in the database.

    Args:
        ops_test: The ops test framework
        users_that_should_exist: List of users that should exist in the database
        users_that_should_not_exist: List of users that should not exist in the database
        admin: Whether to check if the existing users are superusers
        database_app_name: Optional database app name
            (the default value is the name on metadata.yaml)
    """
    unit = ops_test.model.applications[database_app_name].units[0]
    unit_address = await get_unit_address(ops_test, unit.name)
    password = await get_password(ops_test, database_app_name=database_app_name)

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


def construct_endpoint(endpoint: str, region: str) -> str:
    """Construct the S3 service endpoint using the region.

    This is needed when the provided endpoint is from AWS, and it doesn't contain the region.
    """
    # Load endpoints data.
    loader = botocore.loaders.create_loader()
    data = loader.load_data("endpoints")

    # Construct the endpoint using the region.
    resolver = botocore.regions.EndpointResolver(data)
    endpoint_data = resolver.construct_endpoint("s3", region)

    # Use the built endpoint if it is an AWS endpoint.
    if endpoint_data and endpoint.endswith(endpoint_data["dnsSuffix"]):
        endpoint = f'{endpoint.split("://")[0]}://{endpoint_data["hostname"]}'

    return endpoint


def convert_records_to_dict(records: List[tuple]) -> dict:
    """Converts psycopg2 records list to a dict."""
    records_dict = {}
    for record in records:
        # Add record tuple data to dict.
        records_dict[record[0]] = record[1]
    return records_dict


def db_connect(host: str, password: str):
    """Returns psycopg2 connection object linked to postgres db in the given host.

    Args:
        host: the IP of the postgres host container
        password: postgres password

    Returns:
        psycopg2 connection object linked to postgres db, under "operator" user.
    """
    return psycopg2.connect(
        f"dbname='postgres' user='operator' host='{host}' password='{password}' connect_timeout=10"
    )


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


async def enable_connections_logging(ops_test: OpsTest, unit_name: str) -> None:
    """Turn on the log of all connections made to a PostgreSQL instance.

    Args:
        ops_test: The ops test framework instance
        unit_name: The name of the unit to turn on the connection logs
    """
    unit_address = await get_unit_address(ops_test, unit_name)
    requests.patch(
        f"https://{unit_address}:8008/config",
        json={"postgresql": {"parameters": {"log_connections": True}}},
        verify=False,
    )


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


def get_existing_k8s_resources(namespace: str, application: str) -> set:
    """Return the list of k8s resources that were created by the charm and Patroni.

    Args:
        namespace: namespace related to the model where
            the charm was deployed.
        application: application name.

    Returns:
        set of existing charm/Patroni specific k8s resources.
    """
    # Create a k8s API client instance.
    client = Client(namespace=namespace)

    # Retrieve the resources created by the charm and Patroni.
    resources = set()
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


def get_expected_k8s_resources(application: str) -> set:
    """Return the list of expected k8s resources when the charm is deployed.

    Args:
        application: application name.

    Returns:
        set of existing charm/Patroni specific k8s resources.
    """
    # Return the resources that should have been created by the charm and Patroni.
    return {
        f"Endpoints/patroni-{application}",
        f"Endpoints/patroni-{application}-config",
        f"Endpoints/patroni-{application}-sync",
        f"Endpoints/{application}",
        f"Endpoints/{application}-primary",
        f"Endpoints/{application}-replicas",
        f"Service/patroni-{application}-config",
        f"Service/{application}",
        f"Service/{application}-primary",
        f"Service/{application}-replicas",
    }


async def get_password(
    ops_test: OpsTest,
    username: str = "operator",
    database_app_name: str = DATABASE_APP_NAME,
    down_unit: str = None,
):
    """Retrieve a user password using the action."""
    for unit in ops_test.model.applications[database_app_name].units:
        if unit.name != down_unit:
            action = await unit.run_action("get-password", **{"username": username})
            result = await action.wait()
            return result.results["password"]


@retry(
    retry=retry_if_exception(KeyError),
    stop=stop_after_attempt(10),
    wait=wait_exponential(multiplier=1, min=2, max=30),
)
async def get_primary(
    ops_test: OpsTest, database_app_name: str = DATABASE_APP_NAME, down_unit: str = None
) -> str:
    """Get the primary unit.

    Args:
        ops_test: ops_test instance.
        database_app_name: name of the application.
        down_unit: stopped unit to ignore when calling the action.

    Returns:
        the current primary unit.
    """
    for unit in ops_test.model.applications[database_app_name].units:
        if unit.name != down_unit:
            action = await unit.run_action("get-primary")
            action = await action.wait()
            primary = action.results.get("primary", "None")
            if primary == "None":
                continue
            return primary


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


async def check_tls_patroni_api(ops_test: OpsTest, unit_name: str, enabled: bool) -> bool:
    """Returns whether TLS is enabled on Patroni REST API.

    Args:
        ops_test: The ops test framework instance.
        unit_name: The name of the unit where Patroni is running.
        enabled: check if TLS is enabled/disabled

    Returns:
        Whether TLS is enabled/disabled on Patroni REST API.
    """
    unit_address = await get_unit_address(ops_test, unit_name)
    try:
        for attempt in Retrying(
            stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
        ):
            with attempt:
                # 'verify=False' is used here because the unit IP that is used in the test
                # doesn't match the certificate hostname (that is a k8s hostname).
                health_info = requests.get(
                    f"{'https' if enabled else 'http'}://{unit_address}:8008/health",
                    verify=False,
                )
                return health_info.status_code == 200
    except RetryError:
        return False


def has_relation_exited(ops_test: OpsTest, endpoint_one: str, endpoint_two: str) -> bool:
    """Returns true if the relation between endpoint_one and endpoint_two has been removed."""
    for rel in ops_test.model.relations:
        endpoints = [endpoint.name for endpoint in rel.endpoints]
        if endpoint_one not in endpoints and endpoint_two not in endpoints:
            return True
    return False


@retry(
    retry=retry_if_result(lambda x: not x),
    stop=stop_after_attempt(10),
    wait=wait_exponential(multiplier=1, min=2, max=30),
)
async def primary_changed(ops_test: OpsTest, old_primary: str) -> bool:
    """Checks whether the primary unit has changed.

    Args:
        ops_test: The ops test framework instance
        old_primary: The name of the unit that was the primary before.
    """
    application = old_primary.split("/")[0]
    primary = await get_primary(ops_test, application, down_unit=old_primary)
    return primary != old_primary and primary != "None"


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


async def run_command_on_unit(ops_test: OpsTest, unit_name: str, command: str) -> str:
    """Run a command on a specific unit.

    Args:
        ops_test: The ops test framework instance
        unit_name: The name of the unit to run the command on
        command: The command to run

    Returns:
        the command output if it succeeds, otherwise raises an exception.
    """
    complete_command = f"ssh --container postgresql {unit_name} {command}"
    return_code, stdout, _ = await ops_test.juju(*complete_command.split())
    if return_code != 0:
        raise Exception(
            "Expected command %s to succeed instead it failed: %s", command, return_code
        )
    return stdout


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


async def wait_for_idle_on_blocked(
    ops_test: OpsTest,
    database_app_name: str,
    unit_number: int,
    other_app_name: str,
    status_message: str,
):
    """Wait for specific applications becoming idle and blocked together."""
    unit = ops_test.model.units.get(f"{database_app_name}/{unit_number}")
    await asyncio.gather(
        ops_test.model.wait_for_idle(apps=[other_app_name], status="active"),
        ops_test.model.wait_for_idle(
            apps=[database_app_name], status="blocked", raise_on_blocked=False
        ),
        ops_test.model.block_until(lambda: unit.workload_status_message == status_message),
    )


def wait_for_relation_removed_between(
    ops_test: OpsTest, endpoint_one: str, endpoint_two: str
) -> None:
    """Wait for relation to be removed before checking if it's waiting or idle.

    Args:
        ops_test: running OpsTest instance
        endpoint_one: one endpoint of the relation. Doesn't matter if it's provider or requirer.
        endpoint_two: the other endpoint of the relation.
    """
    try:
        for attempt in Retrying(stop=stop_after_delay(3 * 60), wait=wait_fixed(3)):
            with attempt:
                if has_relation_exited(ops_test, endpoint_one, endpoint_two):
                    break
    except RetryError:
        assert False, "Relation failed to exit after 3 minutes."
