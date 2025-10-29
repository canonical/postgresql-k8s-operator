#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio
import itertools
import json
import logging
from asyncio import sleep
from datetime import datetime
from multiprocessing import ProcessError
from pathlib import Path
from subprocess import check_call

import botocore
import psycopg2
import requests
import yaml
from juju.model import Model
from juju.unit import Unit
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

from constants import DATABASE_DEFAULT_NAME, PEER, SYSTEM_USERS_PASSWORD_CONFIG

CHARM_BASE = "ubuntu@22.04"
CHARM_BASE_NOBLE = "ubuntu@24.04"
METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
DATABASE_APP_NAME = METADATA["name"]
APPLICATION_NAME = "postgresql-test-app"
DATA_INTEGRATOR_APP_NAME = "data-integrator"
STORAGE_PATH = METADATA["storage"]["data"]["location"]


class SecretNotFoundError(Exception):
    """Raised when a secret is not found."""


try:
    check_call(["kubectl", "version", "--client=true"])
    KUBECTL = "kubectl"
except FileNotFoundError:
    KUBECTL = "microk8s kubectl"

logger = logging.getLogger(__name__)


async def app_name(
    ops_test: OpsTest, application_name: str = "postgresql-k8s", model: Model = None
) -> str | None:
    """Returns the name of the cluster running PostgreSQL.

    This is important since not all deployments of the PostgreSQL charm have the application name
    "postgresql-k8s".

    Note: if multiple clusters are running PostgreSQL this will return the one first found.
    """
    if model is None:
        model = ops_test.model
    status = await model.get_status()
    for app in model.applications:
        if application_name in status["applications"][app]["charm"]:
            return app

    return None


async def build_and_deploy(
    ops_test: OpsTest,
    charm,
    num_units: int,
    database_app_name: str = DATABASE_APP_NAME,
    wait_for_idle: bool = True,
    status: str = "active",
    model: Model = None,
    extra_config: dict[str, str] | None = None,
) -> None:
    """Builds the charm and deploys a specified number of units."""
    if model is None:
        model = ops_test.model
    if not extra_config:
        extra_config = {}

    # It is possible for users to provide their own cluster for testing. Hence, check if there
    # is a pre-existing cluster.
    if await app_name(ops_test, database_app_name, model):
        return

    resources = {
        "postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"],
    }
    await model.deploy(
        charm,
        resources=resources,
        application_name=database_app_name,
        trust=True,
        num_units=num_units,
        base=CHARM_BASE_NOBLE,
        config={**extra_config, "profile": "testing"},
    )
    if wait_for_idle:
        # Wait until the PostgreSQL charm is successfully deployed.
        await model.wait_for_idle(
            apps=[database_app_name],
            status=status,
            raise_on_blocked=True,
            timeout=1000,
            wait_for_exact_units=num_units,
        )


def check_connected_user(
    cursor, session_user: str, current_user: str, primary: bool = True
) -> None:
    cursor.execute("SELECT session_user,current_user;")
    result = cursor.fetchone()
    if result is not None:
        instance = "primary" if primary else "replica"
        assert result[0] == session_user, (
            f"The session user should be the {session_user} user in the {instance} (it's currently {result[0]})"
        )
        assert result[1] == current_user, (
            f"The current user should be the {current_user} user in the {instance} (it's currently {result[1]})"
        )
    else:
        assert False, "No result returned from the query"


async def check_database_users_existence(
    ops_test: OpsTest,
    users_that_should_exist: list[str],
    users_that_should_not_exist: list[str],
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
        (
            "SELECT CONCAT(usename, ':', usesuper) FROM pg_catalog.pg_user;"
            if admin
            else "SELECT usename FROM pg_catalog.pg_user;"
        ),
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


async def check_database_creation(
    ops_test: OpsTest, database: str, database_app_name: str = DATABASE_APP_NAME
) -> None:
    """Checks that database and tables are successfully created for the application.

    Args:
        ops_test: The ops test framework
        database: Name of the database that should have been created
        database_app_name: Application name of the database charm
    """
    password = await get_password(ops_test, database_app_name=database_app_name)

    for unit in ops_test.model.applications[database_app_name].units:
        unit_address = await get_unit_address(ops_test, unit.name)

        for attempt in Retrying(stop=stop_after_attempt(30), wait=wait_fixed(2), reraise=True):
            with attempt:
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
    health_info = requests.get(f"https://{unit_ip}:8008/health", verify=False).json()
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
        endpoint = f"{endpoint.split('://')[0]}://{endpoint_data['hostname']}"

    return endpoint


def convert_records_to_dict(records: list[tuple]) -> dict:
    """Converts psycopg2 records list to a dict."""
    records_dict = {}
    for record in records:
        # Add record tuple data to dict.
        records_dict[record[0]] = record[1]
    return records_dict


async def count_switchovers(ops_test: OpsTest, unit_name: str) -> int:
    """Return the number of performed switchovers."""
    unit_address = await get_unit_address(ops_test, unit_name)
    switchover_history_info = requests.get(f"https://{unit_address}:8008/history", verify=False)
    return len(switchover_history_info.json())


def db_connect(
    host: str, password: str, user: str = "operator", database: str = "postgres"
) -> psycopg2.extensions.connection:
    """Returns psycopg2 connection object linked to postgres db in the given host.

    Args:
        host: the IP of the postgres host container
        password: postgres password
        user: postgres user (default: operator)
        database: postgres database (default: postgres)

    Returns:
        psycopg2 connection object linked to postgres db, under "operator" user.
    """
    return psycopg2.connect(
        f"dbname='{database}' user='{user}' host='{host}' password='{password}' connect_timeout=10"
    )


async def deploy_and_relate_application_with_postgresql(
    ops_test: OpsTest,
    charm: str,
    application_name: str,
    number_of_units: int,
    channel: str = "stable",
    relation: str = "db",
    status: str = "blocked",
    base: str = CHARM_BASE,
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
        base: The base of the charm to deploy

    Returns:
        the id of the created relation.
    """
    # Deploy application.
    await ops_test.model.deploy(
        charm,
        channel=channel,
        application_name=application_name,
        num_units=number_of_units,
        base=base,
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
        raise_on_error=False,
        timeout=1000,
    )

    return relation.id


async def execute_query_on_unit(
    unit_address: str,
    password: str,
    query: str,
    database: str = DATABASE_DEFAULT_NAME,
    sslmode: str | None = None,
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
    with (
        psycopg2.connect(
            f"dbname='{database}' user='operator' host='{unit_address}'"
            f"password='{password}' connect_timeout=10{extra_connection_parameters}"
        ) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(query)
        output = list(itertools.chain(*cursor.fetchall()))
    return output


def get_cluster_members(endpoint: str) -> list[str]:
    """List of current Patroni cluster members.

    Args:
        endpoint: endpoint of the Patroni API

    Returns:
        list of Patroni cluster members
    """
    r = requests.get(f"https://{endpoint}:8008/cluster", verify=False)
    return [member["name"] for member in r.json()["members"]]


def get_application_units(ops_test: OpsTest, application_name: str) -> list[str]:
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
        resources.update({f"{kind.__name__}/{x.metadata.name}" for x in extra_resources})

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
        f"Endpoints/{application}-primary",
        f"Endpoints/{application}-replicas",
        f"Service/patroni-{application}-config",
        f"Service/{application}-primary",
        f"Service/{application}-replicas",
    }


async def get_leader_unit(ops_test: OpsTest, app: str, model: Model = None) -> Unit | None:
    leader_unit = None
    if model is None:
        model = ops_test.model
    for unit in model.applications[app].units:
        if await unit.is_leader_from_status():
            leader_unit = unit
            break

    return leader_unit


async def get_password(
    ops_test: OpsTest,
    username: str = "operator",
    database_app_name: str = DATABASE_APP_NAME,
):
    """Retrieve a user password from the secret."""
    secret = await get_secret_by_label(ops_test, label=f"{PEER}.{database_app_name}.app")
    password = secret.get(f"{username}-password")

    return password


async def get_secret_by_label(ops_test: OpsTest, label: str) -> dict[str, str]:
    secrets_raw = await ops_test.juju("list-secrets")
    secret_ids = [
        secret_line.split()[0] for secret_line in secrets_raw[1].split("\n")[1:] if secret_line
    ]

    for secret_id in secret_ids:
        secret_data_raw = await ops_test.juju(
            "show-secret", "--format", "json", "--reveal", secret_id
        )
        secret_data = json.loads(secret_data_raw[1])

        if label == secret_data[secret_id].get("label"):
            return secret_data[secret_id]["content"]["Data"]

    raise SecretNotFoundError(f"Secret with label {label} not found")


@retry(
    retry=retry_if_exception(KeyError),
    stop=stop_after_attempt(10),
    wait=wait_exponential(multiplier=1, min=2, max=30),
)
async def get_primary(
    ops_test: OpsTest, database_app_name: str = DATABASE_APP_NAME, down_unit: str | None = None
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


def get_unit_by_index(app: str, units: list, index: int) -> Unit | None:
    """Get unit by index.

    Args:
        app: Name of the application
        units: List of units
        index: index of the unit to get
    """
    for unit in units:
        if unit.name == f"{app}/{index}":
            return unit


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


async def check_tls_replication(ops_test: OpsTest, unit_name: str, enabled: bool) -> bool:
    """Returns whether TLS is enabled on the replica PostgreSQL instance.

    Args:
        ops_test: The ops test framework instance.
        unit_name: The name of the replica of the PostgreSQL instance.
        enabled: check if TLS is enabled/disabled

    Returns:
        Whether TLS is enabled/disabled.
    """
    unit_address = await get_unit_address(ops_test, unit_name)
    password = await get_password(ops_test)

    # Check for the all replicas using encrypted connection
    output = await execute_query_on_unit(
        unit_address,
        password,
        "SELECT pg_ssl.ssl, pg_sa.client_addr FROM pg_stat_ssl pg_ssl"
        " JOIN pg_stat_activity pg_sa ON pg_ssl.pid = pg_sa.pid"
        " AND pg_sa.usename = 'replication';",
    )

    return all(output[i] == enabled for i in range(0, len(output), 2))


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
                    f"https://{unit_address}:8008/health",
                    verify=False,
                )
                return health_info.status_code == 200
    except RetryError:
        return False


def has_relation_exited(
    ops_test: OpsTest, endpoint_one: str, endpoint_two: str, model: Model = None
) -> bool:
    """Returns true if the relation between endpoint_one and endpoint_two has been removed."""
    relations = model.relations if model is not None else ops_test.model.relations
    for rel in relations:
        endpoints = [endpoint.name for endpoint in rel.endpoints]
        if endpoint_one in endpoints and endpoint_two in endpoints:
            return False
    return True


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


async def restart_patroni(ops_test: OpsTest, unit_name: str, password: str) -> None:
    """Restart Patroni on a specific unit.

    Args:
        ops_test: The ops test framework instance
        unit_name: The name of the unit
        password: patroni password
    """
    unit_ip = await get_unit_address(ops_test, unit_name)
    requests.post(
        f"https://{unit_ip}:8008/restart",
        verify=False,
        auth=requests.auth.HTTPBasicAuth("patroni", password),
    )


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


async def run_command_on_unit(
    ops_test: OpsTest, unit_name: str, command: str, container: str = "postgresql"
) -> str:
    """Run a command on a specific unit.

    Args:
        ops_test: The ops test framework instance
        unit_name: The name of the unit to run the command on
        command: The command to run
        container: The container to run the command in (default: postgresql)

    Returns:
        the command output if it succeeds, otherwise raises an exception.
    """
    complete_command = f"ssh --container {container} {unit_name} {command}"
    return_code, stdout, stderr = await ops_test.juju(*complete_command.split())
    if return_code != 0:
        raise Exception(
            f"Expected command {command} to succeed instead it failed: {stderr}. Code: {return_code}"
        )
    return stdout


async def scale_application(
    ops_test: OpsTest, application_name: str, scale: int, model: Model = None
) -> None:
    """Scale a given application to a specific unit count.

    Args:
        ops_test: The ops test framework instance
        application_name: The name of the application
        scale: The number of units to scale to
        model: The model to scale the application in
    """
    if model is None:
        model = ops_test.model
    await model.applications[application_name].scale(scale)
    if scale == 0:
        await model.block_until(
            lambda: len(model.applications[application_name].units) == scale,
            timeout=1000,
        )
    else:
        await model.wait_for_idle(
            apps=[application_name],
            status="active",
            timeout=1000,
            wait_for_exact_units=scale,
        )


async def set_password(
    ops_test: OpsTest,
    app_name: str = DATABASE_APP_NAME,
    username: str = "operator",
    password: str | None = None,
):
    """Set a user password via secret."""
    secret_name = "system_users_secret"

    try:
        secret_id = await ops_test.model.add_secret(
            name=secret_name, data_args=[f"{username}={password}"]
        )
        await ops_test.model.grant_secret(secret_name=secret_name, application=app_name)

        # update the application config to include the secret
        await ops_test.model.applications[app_name].set_config({
            SYSTEM_USERS_PASSWORD_CONFIG: secret_id
        })
    except Exception:
        await ops_test.model.update_secret(
            name=secret_name, data_args=[f"{username}={password}"], new_name=secret_name
        )


async def switchover(
    ops_test: OpsTest, current_primary: str, password: str, candidate: str | None = None
) -> None:
    """Trigger a switchover.

    Args:
        ops_test: The ops test framework instance.
        current_primary: The current primary unit.
        password: Patroni password.
        candidate: The unit that should be elected the new primary.
    """
    primary_ip = await get_unit_address(ops_test, current_primary)
    for attempt in Retrying(stop=stop_after_attempt(60), wait=wait_fixed(3), reraise=True):
        with attempt:
            response = requests.post(
                f"https://{primary_ip}:8008/switchover",
                json={
                    "leader": current_primary.replace("/", "-"),
                    "candidate": candidate.replace("/", "-") if candidate else None,
                },
                verify=False,
                auth=requests.auth.HTTPBasicAuth("patroni", password),
            )
            assert response.status_code == 200, f"Switchover status code is {response.status_code}"
    app_name = current_primary.split("/")[0]
    for attempt in Retrying(stop=stop_after_attempt(30), wait=wait_fixed(2), reraise=True):
        with attempt:
            response = requests.get(f"https://{primary_ip}:8008/cluster", verify=False)
            assert response.status_code == 200
            standbys = len([
                member for member in response.json()["members"] if member["role"] == "sync_standby"
            ])
            assert standbys == len(ops_test.model.applications[app_name].units) - 1


async def switchover_to_unit_zero(ops_test: OpsTest) -> None:
    primary_name = await get_primary(ops_test, DATABASE_APP_NAME)
    expected_primary_name = f"{DATABASE_APP_NAME}/0"
    if primary_name != expected_primary_name:
        logger.info(f"Switching primary to {expected_primary_name}")
        action = await ops_test.model.units[expected_primary_name].run_action(
            "promote-to-primary", scope="unit"
        )
        await action.wait()

        await sleep(30)

        primary_name = await get_primary(ops_test, DATABASE_APP_NAME)
        assert primary_name == expected_primary_name, "Primary unit not set to unit 0"


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
        ops_test.model.block_until(
            lambda: unit.workload_status == "blocked"
            and unit.workload_status_message == status_message
        ),
    )


def wait_for_relation_removed_between(
    ops_test: OpsTest, endpoint_one: str, endpoint_two: str, model: Model = None
) -> None:
    """Wait for relation to be removed before checking if it's waiting or idle.

    Args:
        ops_test: running OpsTest instance
        endpoint_one: one endpoint of the relation. Doesn't matter if it's provider or requirer.
        endpoint_two: the other endpoint of the relation.
        model: optional model to check for the relation.
    """
    try:
        for attempt in Retrying(stop=stop_after_delay(3 * 60), wait=wait_fixed(3)):
            with attempt:
                if has_relation_exited(ops_test, endpoint_one, endpoint_two, model):
                    break
    except RetryError:
        assert False, "Relation failed to exit after 3 minutes."


async def cat_file_from_unit(ops_test: OpsTest, filepath: str, unit_name: str) -> str:
    """Gets a file from the postgresql container of an application unit."""
    cat_cmd = f"ssh --container postgresql {unit_name} cat {filepath}"
    return_code, output, _ = await ops_test.juju(*cat_cmd.split(" "))
    if return_code != 0:
        raise ProcessError(
            "Expected cat command %s to succeed instead it failed: %s", cat_cmd, return_code
        )
    return output


async def backup_operations(
    ops_test: OpsTest,
    charm,
    s3_integrator_app_name: str,
    tls_certificates_app_name: str,
    tls_config,
    tls_channel,
    credentials,
    cloud,
    config,
) -> None:
    """Basic set of operations for backup testing in different cloud providers."""
    # Deploy S3 Integrator and TLS Certificates Operator.
    use_tls = all([tls_certificates_app_name, tls_config, tls_channel])
    await ops_test.model.deploy(s3_integrator_app_name, base=CHARM_BASE)
    if use_tls:
        await ops_test.model.deploy(
            tls_certificates_app_name, config=tls_config, channel=tls_channel, base=CHARM_BASE
        )
    # Deploy and relate PostgreSQL to S3 integrator (one database app for each cloud for now
    # as archivo_mode is disabled after restoring the backup) and to TLS Certificates Operator
    # (to be able to create backups from replicas).
    database_app_name = f"{DATABASE_APP_NAME}-{cloud.lower()}"
    await build_and_deploy(
        ops_test, charm, 2, database_app_name=database_app_name, wait_for_idle=False
    )

    if use_tls:
        await ops_test.model.relate(
            f"{database_app_name}:peer-certificates", f"{tls_certificates_app_name}:certificates"
        )
        await ops_test.model.relate(
            f"{database_app_name}:client-certificates", f"{tls_certificates_app_name}:certificates"
        )
    async with ops_test.fast_forward(fast_interval="60s"):
        await ops_test.model.wait_for_idle(
            apps=[database_app_name], status="active", timeout=1000, raise_on_error=False
        )
    await ops_test.model.relate(database_app_name, s3_integrator_app_name)

    # Configure and set access and secret keys.
    logger.info(f"configuring S3 integrator for {cloud}")
    await ops_test.model.applications[s3_integrator_app_name].set_config(config)
    action = await ops_test.model.units.get(f"{s3_integrator_app_name}/0").run_action(
        "sync-s3-credentials",
        **credentials,
    )
    await action.wait()
    async with ops_test.fast_forward(fast_interval="60s"):
        await ops_test.model.wait_for_idle(
            apps=[database_app_name, s3_integrator_app_name], status="active", timeout=1000
        )

    primary = await get_primary(ops_test, database_app_name)
    for unit in ops_test.model.applications[database_app_name].units:
        if unit.name != primary:
            replica = unit.name
            break

    # Write some data.
    password = await get_password(ops_test, database_app_name=database_app_name)
    address = await get_unit_address(ops_test, primary)
    logger.info("creating a table in the database")
    with db_connect(host=address, password=password) as connection:
        connection.autocommit = True
        connection.cursor().execute(
            "CREATE TABLE IF NOT EXISTS backup_table_1 (test_collumn INT );"
        )
    connection.close()

    # With a stable cluster, Run the "create backup" action
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(status="active", timeout=1000, idle_period=30)
    logger.info("creating a backup")
    action = await ops_test.model.units.get(replica).run_action("create-backup")
    await action.wait()
    backup_status = action.results.get("backup-status")
    assert backup_status, "backup hasn't succeeded"
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(status="active", timeout=1000)

    # Run the "list backups" action.
    logger.info("listing the available backups")
    action = await ops_test.model.units.get(replica).run_action("list-backups")
    await action.wait()
    backups = action.results.get("backups")
    # 5 lines for header output, 1 backup line ==> 6 total lines
    assert len(backups.split("\n")) == 6, "full backup is not outputted"
    await ops_test.model.wait_for_idle(status="active", timeout=1000)

    # Write some data.
    logger.info("creating a second table in the database")
    with db_connect(host=address, password=password) as connection:
        connection.autocommit = True
        connection.cursor().execute("CREATE TABLE backup_table_2 (test_collumn INT );")
    connection.close()

    # Run the "create backup" action.
    logger.info("creating a backup")
    action = await ops_test.model.units.get(replica).run_action(
        "create-backup", **{"type": "differential"}
    )
    await action.wait()
    backup_status = action.results.get("backup-status")
    assert backup_status, "backup hasn't succeeded"
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(status="active", timeout=1000)

    # Run the "list backups" action.
    logger.info("listing the available backups")
    action = await ops_test.model.units.get(replica).run_action("list-backups")
    await action.wait()
    backups = action.results.get("backups")
    # 5 lines for header output, 2 backup lines ==> 7 total lines
    assert len(backups.split("\n")) == 7, "differential backup is not outputted"
    await ops_test.model.wait_for_idle(status="active", timeout=1000)

    # Write some data.
    logger.info("creating a second table in the database")
    with db_connect(host=address, password=password) as connection:
        connection.autocommit = True
        connection.cursor().execute("CREATE TABLE backup_table_3 (test_collumn INT );")
    connection.close()
    # Scale down to be able to restore.
    async with ops_test.fast_forward(fast_interval="60s"):
        await scale_application(ops_test, database_app_name, 1)

    remaining_unit = ops_test.model.units.get(f"{database_app_name}/0")

    # Run the "restore backup" action for differential backup.
    for attempt in Retrying(
        stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
    ):
        with attempt:
            logger.info("restoring the backup")
            last_diff_backup = backups.split("\n")[-1]
            backup_id = last_diff_backup.split()[0]
            action = await remaining_unit.run_action("restore", **{"backup-id": backup_id})
            await action.wait()
            restore_status = action.results.get("restore-status")
            assert restore_status, "restore hasn't succeeded"

    # Wait for the restore to complete.
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(status="active", timeout=1000)

    # Check that the backup was correctly restored by having only the first created table.
    logger.info("checking that the backup was correctly restored")
    primary = await get_primary(ops_test, database_app_name)
    address = await get_unit_address(ops_test, primary)
    with db_connect(host=address, password=password) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_name = 'backup_table_1');"
        )
        assert cursor.fetchone()[0], (
            "backup wasn't correctly restored: table 'backup_table_1' doesn't exist"
        )
        cursor.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_name = 'backup_table_2');"
        )
        assert cursor.fetchone()[0], (
            "backup wasn't correctly restored: table 'backup_table_2' doesn't exist"
        )
        cursor.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_name = 'backup_table_3');"
        )
        assert not cursor.fetchone()[0], (
            "backup wasn't correctly restored: table 'backup_table_3' exists"
        )
    connection.close()

    # Run the "restore backup" action for full backup.
    for attempt in Retrying(
        stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
    ):
        with attempt:
            logger.info("restoring the backup")
            last_full_backup = backups.split("\n")[-2]
            backup_id = last_full_backup.split()[0]
            action = await remaining_unit.run_action("restore", **{"backup-id": backup_id})
            await action.wait()
            restore_status = action.results.get("restore-status")
            assert restore_status, "restore hasn't succeeded"

    # Wait for the restore to complete.
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(status="active", timeout=1000)

    # Check that the backup was correctly restored by having only the first created table.
    logger.info("checking that the backup was correctly restored")
    primary = await get_primary(ops_test, database_app_name)
    address = await get_unit_address(ops_test, primary)
    with db_connect(host=address, password=password) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_name = 'backup_table_1');"
        )
        assert cursor.fetchone()[0], (
            "backup wasn't correctly restored: table 'backup_table_1' doesn't exist"
        )
        cursor.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_name = 'backup_table_2');"
        )
        assert not cursor.fetchone()[0], (
            "backup wasn't correctly restored: table 'backup_table_2' exists"
        )
        cursor.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_name = 'backup_table_3');"
        )
        assert not cursor.fetchone()[0], (
            "backup wasn't correctly restored: table 'backup_table_3' exists"
        )
    connection.close()
