# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio
import logging
from pathlib import Path

import psycopg2
import pytest
import yaml
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_attempt, wait_fixed

from constants import DATABASE_DEFAULT_NAME

from ..helpers import (
    CHARM_BASE,
    DATA_INTEGRATOR_APP_NAME,
    check_database_users_existence,
    scale_application,
)
from .helpers import (
    build_connection_string,
    get_application_relation_data,
)

logger = logging.getLogger(__name__)

APPLICATION_APP_NAME = "postgresql-test-app"
DATABASE_APP_NAME = "database"
ANOTHER_DATABASE_APP_NAME = "another-database"
DISCOURSE_APP_NAME = "discourse-k8s"
APP_NAMES = [APPLICATION_APP_NAME, DATABASE_APP_NAME, ANOTHER_DATABASE_APP_NAME]
DATABASE_APP_METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
FIRST_DATABASE_RELATION_NAME = "database"
SECOND_DATABASE_RELATION_NAME = "second-database"
MULTIPLE_DATABASE_CLUSTERS_RELATION_NAME = "multiple-database-clusters"
ALIASED_MULTIPLE_DATABASE_CLUSTERS_RELATION_NAME = "aliased-multiple-database-clusters"
NO_DATABASE_RELATION_NAME = "no-database"
INVALID_EXTRA_USER_ROLE_BLOCKING_MESSAGE = "invalid role(s) for extra user roles"


@pytest.mark.abort_on_fail
async def test_database_relation_with_charm_libraries(ops_test: OpsTest, charm):
    """Test basic functionality of database relation interface."""
    # Deploy both charms (multiple units for each application to test that later they correctly
    # set data in the relation application databag using only the leader unit).
    async with ops_test.fast_forward():
        await asyncio.gather(
            ops_test.model.deploy(
                APPLICATION_APP_NAME,
                application_name=APPLICATION_APP_NAME,
                num_units=2,
                base=CHARM_BASE,
                channel="latest/edge",
            ),
            ops_test.model.deploy(
                charm,
                resources={
                    "postgresql-image": DATABASE_APP_METADATA["resources"]["postgresql-image"][
                        "upstream-source"
                    ]
                },
                application_name=DATABASE_APP_NAME,
                num_units=3,
                base=CHARM_BASE,
                trust=True,
                config={"profile": "testing"},
            ),
            ops_test.model.deploy(
                charm,
                resources={
                    "postgresql-image": DATABASE_APP_METADATA["resources"]["postgresql-image"][
                        "upstream-source"
                    ]
                },
                application_name=ANOTHER_DATABASE_APP_NAME,
                num_units=3,
                base=CHARM_BASE,
                trust=True,
                config={"profile": "testing"},
            ),
        )
        # Relate the charms and wait for them exchanging some connection data.
        await ops_test.model.add_relation(
            f"{APPLICATION_APP_NAME}:{FIRST_DATABASE_RELATION_NAME}", DATABASE_APP_NAME
        )
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME],
            status="active",
            raise_on_blocked=True,
            raise_on_error=False,
            timeout=1000,
        )

        # Check that on juju 3 we have secrets and no username and password in the rel databag
        if hasattr(ops_test.model, "list_secrets"):
            logger.info("checking for secrets")
            secret_uri, password = await asyncio.gather(
                get_application_relation_data(
                    ops_test,
                    APPLICATION_APP_NAME,
                    FIRST_DATABASE_RELATION_NAME,
                    "secret-user",
                ),
                get_application_relation_data(
                    ops_test,
                    APPLICATION_APP_NAME,
                    FIRST_DATABASE_RELATION_NAME,
                    "password",
                ),
            )
            assert secret_uri is not None
            assert password is None

    # Get the connection string to connect to the database using the read/write endpoint.
    connection_string = await build_connection_string(
        ops_test, APPLICATION_APP_NAME, FIRST_DATABASE_RELATION_NAME
    )

    # Connect to the database using the read/write endpoint.
    with psycopg2.connect(connection_string) as connection, connection.cursor() as cursor:
        # Check that it's possible to write and read data from the database that
        # was created for the application.
        connection.autocommit = True
        cursor.execute("DROP TABLE IF EXISTS test;")
        cursor.execute("CREATE TABLE test(data TEXT);")
        cursor.execute("INSERT INTO test(data) VALUES('some data');")
        cursor.execute("SELECT data FROM test;")
        data = cursor.fetchone()
        assert data[0] == "some data"

        # Check the version that the application received is the same on the database server.
        cursor.execute("SELECT version();")
        data = cursor.fetchone()[0].split(" ")[1]

        # Get the version of the database and compare with the information that
        # was retrieved directly from the database.
        version = await get_application_relation_data(
            ops_test, APPLICATION_APP_NAME, FIRST_DATABASE_RELATION_NAME, "version"
        )
        assert version == data

    # Get the connection string to connect to the database using the read-only endpoint.
    connection_string = await build_connection_string(
        ops_test, APPLICATION_APP_NAME, FIRST_DATABASE_RELATION_NAME, read_only_endpoint=True
    )

    # Connect to the database using the read-only endpoint.
    with psycopg2.connect(connection_string) as connection, connection.cursor() as cursor:
        # Read some data.
        cursor.execute("SELECT data FROM test;")
        data = cursor.fetchone()
        assert data[0] == "some data"

        # Try to alter some data in a read-only transaction.
        with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
            cursor.execute("DROP TABLE test;")


async def test_two_applications_doesnt_share_the_same_relation_data(ops_test: OpsTest):
    """Test that two different application connect to the database with different credentials."""
    # Set some variables to use in this test.
    another_application_app_name = "another-application"
    all_app_names = [another_application_app_name]
    all_app_names.extend(APP_NAMES)

    # Deploy another application.
    await ops_test.model.deploy(
        APPLICATION_APP_NAME,
        application_name=another_application_app_name,
        base=CHARM_BASE,
        channel="latest/edge",
    )

    # Relate the new application with the database
    # and wait for them exchanging some connection data.
    await ops_test.model.add_relation(
        f"{another_application_app_name}:{FIRST_DATABASE_RELATION_NAME}", DATABASE_APP_NAME
    )
    await ops_test.model.wait_for_idle(apps=all_app_names, status="active", idle_period=30)

    # Assert the two application have different relation (connection) data.
    application_connection_string = await build_connection_string(
        ops_test, APPLICATION_APP_NAME, FIRST_DATABASE_RELATION_NAME
    )
    another_application_connection_string = await build_connection_string(
        ops_test, another_application_app_name, FIRST_DATABASE_RELATION_NAME
    )

    assert application_connection_string != another_application_connection_string

    # Check that the user cannot access other databases.
    for application, other_application_database in [
        (APPLICATION_APP_NAME, "another_application_database"),
        (another_application_app_name, f"{APPLICATION_APP_NAME.replace('-', '_')}_database"),
    ]:
        connection_string = await build_connection_string(
            ops_test,
            application,
            FIRST_DATABASE_RELATION_NAME,
            database=DATABASE_DEFAULT_NAME,
        )
        with pytest.raises(psycopg2.Error):
            psycopg2.connect(connection_string)
        connection_string = await build_connection_string(
            ops_test,
            application,
            FIRST_DATABASE_RELATION_NAME,
            database=other_application_database,
        )
        with pytest.raises(psycopg2.Error):
            psycopg2.connect(connection_string)


async def test_an_application_can_connect_to_multiple_database_clusters(ops_test: OpsTest, charm):
    """Test that an application can connect to different clusters of the same database."""
    # Relate the application with both database clusters
    # and wait for them exchanging some connection data.
    first_cluster_relation = await ops_test.model.add_relation(
        f"{APPLICATION_APP_NAME}:{MULTIPLE_DATABASE_CLUSTERS_RELATION_NAME}", DATABASE_APP_NAME
    )
    second_cluster_relation = await ops_test.model.add_relation(
        f"{APPLICATION_APP_NAME}:{MULTIPLE_DATABASE_CLUSTERS_RELATION_NAME}",
        ANOTHER_DATABASE_APP_NAME,
    )
    await ops_test.model.wait_for_idle(apps=APP_NAMES, status="active", idle_period=30)

    # Retrieve the connection string to both database clusters using the relation aliases
    # and assert they are different.
    application_connection_string = await build_connection_string(
        ops_test,
        APPLICATION_APP_NAME,
        MULTIPLE_DATABASE_CLUSTERS_RELATION_NAME,
        relation_id=first_cluster_relation.id,
    )
    another_application_connection_string = await build_connection_string(
        ops_test,
        APPLICATION_APP_NAME,
        MULTIPLE_DATABASE_CLUSTERS_RELATION_NAME,
        relation_id=second_cluster_relation.id,
    )
    assert application_connection_string != another_application_connection_string


# async def test_an_application_can_connect_to_multiple_aliased_database_clusters(
#     ops_test: OpsTest, charm
# ):
#     """Test that an application can connect to different clusters of the same database."""
#     # Relate the application with both database clusters
#     # and wait for them exchanging some connection data.
#     await asyncio.gather(
#         ops_test.model.add_relation(
#             f"{APPLICATION_APP_NAME}:{ALIASED_MULTIPLE_DATABASE_CLUSTERS_RELATION_NAME}",
#             DATABASE_APP_NAME,
#         ),
#         ops_test.model.add_relation(
#             f"{APPLICATION_APP_NAME}:{ALIASED_MULTIPLE_DATABASE_CLUSTERS_RELATION_NAME}",
#             ANOTHER_DATABASE_APP_NAME,
#         ),
#     )
#     await ops_test.model.wait_for_idle(apps=APP_NAMES, status="active")
#
#     # Retrieve the connection string to both database clusters using the relation aliases
#     # and assert they are different.
#     for attempt in Retrying(stop=stop_after_attempt(5), wait=wait_fixed(3), reraise=True):
#         with attempt:
#             application_connection_string = await build_connection_string(
#                 ops_test,
#                 APPLICATION_APP_NAME,
#                 ALIASED_MULTIPLE_DATABASE_CLUSTERS_RELATION_NAME,
#                 relation_alias="cluster1",
#             )
#             another_application_connection_string = await build_connection_string(
#                 ops_test,
#                 APPLICATION_APP_NAME,
#                 ALIASED_MULTIPLE_DATABASE_CLUSTERS_RELATION_NAME,
#                 relation_alias="cluster2",
#             )
#     assert application_connection_string != another_application_connection_string


@pytest.mark.abort_on_fail
async def test_an_application_can_request_multiple_databases(ops_test: OpsTest):
    """Test that an application can request additional databases using the same interface."""
    # Relate the charms using another relation and wait for them exchanging some connection data.
    await ops_test.model.add_relation(
        f"{APPLICATION_APP_NAME}:{SECOND_DATABASE_RELATION_NAME}", DATABASE_APP_NAME
    )
    await ops_test.model.wait_for_idle(
        apps=APP_NAMES, status="active", timeout=15 * 60, idle_period=30
    )

    # Get the connection strings to connect to both databases.
    for attempt in Retrying(stop=stop_after_attempt(15), wait=wait_fixed(3), reraise=True):
        with attempt:
            first_database_connection_string = await build_connection_string(
                ops_test, APPLICATION_APP_NAME, FIRST_DATABASE_RELATION_NAME
            )
            second_database_connection_string = await build_connection_string(
                ops_test, APPLICATION_APP_NAME, SECOND_DATABASE_RELATION_NAME
            )

    # Assert the two application have different relation (connection) data.
    assert first_database_connection_string != second_database_connection_string


async def test_primary_read_only_endpoint_in_standalone_cluster(ops_test: OpsTest):
    """Test that there is no read-only endpoint in a standalone cluster."""
    async with ops_test.fast_forward():
        # Scale down the database.
        await scale_application(ops_test, DATABASE_APP_NAME, 1)

        # Try to get the connection string of the database using the read-only endpoint.
        # It should not be available anymore.
        for attempt in Retrying(stop=stop_after_attempt(3), wait=wait_fixed(3), reraise=True):
            with attempt:
                data = await get_application_relation_data(
                    ops_test,
                    APPLICATION_APP_NAME,
                    FIRST_DATABASE_RELATION_NAME,
                    "read-only-endpoints",
                )
                assert (
                    data
                    == f"{DATABASE_APP_NAME}-primary.{ops_test.model.name}.svc.cluster.local:5432"
                )


async def test_read_only_endpoint_in_scaled_up_cluster(ops_test: OpsTest):
    """Test that there is read-only endpoint in a scaled up cluster."""
    async with ops_test.fast_forward():
        # Scale up the database.
        await scale_application(ops_test, DATABASE_APP_NAME, 3)

        # Try to get the connection string of the database using the read-only endpoint.
        # It should be available again.
        for attempt in Retrying(stop=stop_after_attempt(3), wait=wait_fixed(3), reraise=True):
            with attempt:
                data = await get_application_relation_data(
                    ops_test,
                    APPLICATION_APP_NAME,
                    FIRST_DATABASE_RELATION_NAME,
                    "read-only-endpoints",
                )
                assert (
                    data
                    == f"{DATABASE_APP_NAME}-replicas.{ops_test.model.name}.svc.cluster.local:5432"
                )


async def test_relation_broken(ops_test: OpsTest):
    """Test that the user is removed when the relation is broken."""
    async with ops_test.fast_forward():
        # Retrieve the relation user.
        relation_user = await get_application_relation_data(
            ops_test, APPLICATION_APP_NAME, FIRST_DATABASE_RELATION_NAME, "username"
        )

        # Break the relation.
        await ops_test.model.applications[DATABASE_APP_NAME].remove_relation(
            f"{DATABASE_APP_NAME}", f"{APPLICATION_APP_NAME}:{FIRST_DATABASE_RELATION_NAME}"
        )
        await ops_test.model.wait_for_idle(apps=APP_NAMES, status="active", raise_on_blocked=True)

        # Check that the relation user was removed from the database.
        await check_database_users_existence(
            ops_test, [], [relation_user], database_app_name=DATABASE_APP_NAME
        )


async def test_restablish_relation(ops_test: OpsTest):
    """Test that a previously broken relation would be functional if restored."""
    # Relate the charms and wait for them exchanging some connection data.
    async with ops_test.fast_forward():
        await ops_test.model.add_relation(
            f"{APPLICATION_APP_NAME}:{FIRST_DATABASE_RELATION_NAME}", DATABASE_APP_NAME
        )
        await ops_test.model.wait_for_idle(
            apps=APP_NAMES, status="active", idle_period=30, raise_on_blocked=True
        )

    # Get the connection string to connect to the database using the read-only endpoint.
    connection_string = await build_connection_string(
        ops_test, APPLICATION_APP_NAME, FIRST_DATABASE_RELATION_NAME, read_only_endpoint=True
    )

    # Connect to the database using the read-only endpoint.
    with psycopg2.connect(connection_string) as connection, connection.cursor() as cursor:
        # Check that preexisting data is still accessible.
        cursor.execute("SELECT data FROM test;")
        data = cursor.fetchone()
        assert data[0] == "some data"

    # Get the connection string to connect to the database using the read/write endpoint.
    connection_string = await build_connection_string(
        ops_test, APPLICATION_APP_NAME, FIRST_DATABASE_RELATION_NAME
    )

    # Connect to the database using the read/write endpoint.
    with psycopg2.connect(connection_string) as connection, connection.cursor() as cursor:
        # Check that it's possible to write and read data from the database.
        connection.autocommit = True
        cursor.execute("DELETE FROM test;")
        cursor.execute("INSERT INTO test(data) VALUES('other data');")
        cursor.execute("SELECT data FROM test;")
        data = cursor.fetchone()
        assert data[0] == "other data"


@pytest.mark.abort_on_fail
async def test_relation_with_no_database_name(ops_test: OpsTest):
    """Test that a relation with no database name doesn't block the charm."""
    async with ops_test.fast_forward():
        # Relate the charms using a relation that doesn't provide a database name.
        await ops_test.model.add_relation(
            f"{APPLICATION_APP_NAME}:{NO_DATABASE_RELATION_NAME}", DATABASE_APP_NAME
        )
        await ops_test.model.wait_for_idle(apps=APP_NAMES, status="active", raise_on_blocked=True)

        # Break the relation.
        await ops_test.model.applications[DATABASE_APP_NAME].remove_relation(
            f"{DATABASE_APP_NAME}", f"{APPLICATION_APP_NAME}:{NO_DATABASE_RELATION_NAME}"
        )
        await ops_test.model.wait_for_idle(apps=APP_NAMES, status="active", raise_on_blocked=True)


@pytest.mark.abort_on_fail
async def test_invalid_extra_user_roles(ops_test: OpsTest):
    async with ops_test.fast_forward():
        await ops_test.model.deploy(
            DATA_INTEGRATOR_APP_NAME, channel="latest/edge", series="noble"
        )
        await ops_test.model.wait_for_idle(apps=[DATA_INTEGRATOR_APP_NAME], status="blocked")

        another_data_integrator_app_name = f"another-{DATA_INTEGRATOR_APP_NAME}"
        data_integrator_apps_names = [DATA_INTEGRATOR_APP_NAME, another_data_integrator_app_name]
        await ops_test.model.deploy(
            DATA_INTEGRATOR_APP_NAME,
            application_name=another_data_integrator_app_name,
            channel="latest/edge",
            series="noble",
        )
        await ops_test.model.wait_for_idle(
            apps=[another_data_integrator_app_name], status="blocked"
        )
        for app in data_integrator_apps_names:
            await ops_test.model.applications[app].set_config({
                "database-name": app.replace("-", "_"),
                "extra-user-roles": "test",
            })
        await ops_test.model.wait_for_idle(apps=data_integrator_apps_names, status="blocked")
        for app in data_integrator_apps_names:
            await ops_test.model.add_relation(f"{app}:postgresql", f"{DATABASE_APP_NAME}:database")
        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME])
        await ops_test.model.block_until(
            lambda: any(
                unit.workload_status_message == INVALID_EXTRA_USER_ROLE_BLOCKING_MESSAGE
                for unit in ops_test.model.applications[DATABASE_APP_NAME].units
            ),
            timeout=1000,
        )

        # Verify that the charm remains blocked if there are still other relations with invalid
        # extra user roles.
        await ops_test.model.applications[DATABASE_APP_NAME].destroy_relation(
            f"{DATABASE_APP_NAME}:database", f"{DATA_INTEGRATOR_APP_NAME}:postgresql"
        )
        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME])
        await ops_test.model.block_until(
            lambda: any(
                unit.workload_status_message == INVALID_EXTRA_USER_ROLE_BLOCKING_MESSAGE
                for unit in ops_test.model.applications[DATABASE_APP_NAME].units
            ),
            timeout=1000,
        )

        # Verify that active status is restored after all relations are removed.
        await ops_test.model.applications[DATABASE_APP_NAME].destroy_relation(
            f"{DATABASE_APP_NAME}:database", f"{another_data_integrator_app_name}:postgresql"
        )
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME],
            status="active",
            raise_on_blocked=False,
            timeout=1000,
        )
