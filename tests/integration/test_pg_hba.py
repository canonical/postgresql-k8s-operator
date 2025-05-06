#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import pytest
from pytest_operator.plugin import OpsTest

from .helpers import (
    DATABASE_APP_NAME,
    build_and_deploy,
    get_unit_address, db_connect, app_name,
)
import psycopg2

logger = logging.getLogger(__name__)

DATA_INTEGRATOR_APP_NAME = "data-integrator"
FIRST_DATABASE = "test_1"
SECOND_DATABASE = "test_2"
FIRST_RELATION_USER = "relation_id_0"
SECOND_RELATION_USER = "relation_id_1"
PASSWORD = "test-password"


@pytest.mark.abort_on_fail
async def test_pg_hba(ops_test: OpsTest, charm):
    async with ops_test.fast_forward():
        logger.info("Deploying charms")
        if not app_name(DATABASE_APP_NAME):
            await build_and_deploy(ops_test, charm, num_units=1)
        if not app_name(DATA_INTEGRATOR_APP_NAME):
            await ops_test.model.deploy(
                DATA_INTEGRATOR_APP_NAME,
                config={"database-name": FIRST_DATABASE, "extra-user-roles": "SUPERUSER"},
            )

        logger.info("Adding relation between charms")
        relations = [
            relation
            for relation in ops_test.model.applications[DATABASE_APP_NAME].relations
            if not relation.is_peer
               and f"{relation.requires.application_name}:{relation.requires.name}"
               == f"{DATA_INTEGRATOR_APP_NAME}:postgresql"
        ]
        if not relations:
            await ops_test.model.add_relation(DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME)

        await ops_test.model.wait_for_idle(
            apps=[DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME], status="active"
        )

        database_units = ops_test.model.applications[DATABASE_APP_NAME].units
        data_integrator_unit = ops_test.model.applications[DATA_INTEGRATOR_APP_NAME].units[0]
        address = await get_unit_address(ops_test, database_units[0].name)
        action = await data_integrator_unit.run_action(action_name="get-credentials")
        result = await action.wait()
        credentials = result.results
        connection = None
        try:
            connection = db_connect(host=address, password=credentials['postgresql']["password"], user=credentials['postgresql']['username'], database=FIRST_DATABASE)
            connection.autocommit = True
            with connection.cursor() as cursor:
                # Drop objects from the previous test run.
                cursor.execute(f"DROP USER IF EXISTS {FIRST_RELATION_USER};")
                cursor.execute(f"SELECT datname FROM pg_database WHERE datname='{SECOND_DATABASE}';")
                if cursor.fetchone() is not None:
                    cursor.execute(f"REVOKE ALL ON DATABASE {SECOND_DATABASE} FROM {SECOND_RELATION_USER};")
                cursor.execute(f"DROP USER IF EXISTS {SECOND_RELATION_USER};")
                cursor.execute(f"DROP DATABASE IF EXISTS {SECOND_DATABASE};")
                cursor.execute("DROP SCHEMA IF EXISTS test;")
                # Create objects needed for the test.
                cursor.execute(f"CREATE USER {FIRST_RELATION_USER} WITH LOGIN SUPERUSER ENCRYPTED PASSWORD '{PASSWORD}';")
                cursor.execute(f"CREATE USER {SECOND_RELATION_USER} WITH LOGIN ENCRYPTED PASSWORD '{PASSWORD}';")
                cursor.execute(f"CREATE DATABASE {SECOND_DATABASE};")
                cursor.execute(f"GRANT CONNECT ON DATABASE {SECOND_DATABASE} TO {SECOND_RELATION_USER};")
                cursor.execute("CREATE SCHEMA test;")
        finally:
            if connection:
                connection.close()

        for unit in database_units:
            try:
                address = await get_unit_address(ops_test, unit.name)
                with db_connect(host=address, password=PASSWORD, user=FIRST_RELATION_USER, database=FIRST_DATABASE) as connection, connection.cursor() as cursor:
                    # Check the version that the application received is the same on the
                    # database server.
                    cursor.execute("SELECT version();")
                    data = cursor.fetchone()[0].split(" ")[1]

                    # Get the version of the database and compare with the information that
                    # was retrieved directly from the database.
                    assert credentials['postgresql']["version"] == data
                connection.close()

                with db_connect(host=address, password=PASSWORD, user=SECOND_RELATION_USER, database=SECOND_DATABASE) as connection, connection.cursor() as cursor:
                    # Check the version that the application received is the same on the
                    # database server.
                    cursor.execute("SELECT VERSION();")

                    # Get the version of the database and compare with the information that
                    # was retrieved directly from the database.
                    assert credentials['postgresql']["version"] == data

                with db_connect(host=address, password=PASSWORD, user=SECOND_RELATION_USER, database=FIRST_DATABASE) as connection:
                    assert False, f"User {SECOND_RELATION_USER} should not be able to connect to the database {FIRST_DATABASE}"
            except psycopg2.OperationalError as e:
                if f'FATAL:  no pg_hba.conf entry for host "159.69.30.76", user "{SECOND_RELATION_USER}", database "{FIRST_DATABASE}", no encryption' not in str(e):
                    raise
            finally:
                if connection:
                    connection.close()
