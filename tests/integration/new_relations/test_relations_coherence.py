#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
import secrets
import string

import psycopg2
import pytest
from pytest_operator.plugin import OpsTest

from ..helpers import DATABASE_APP_NAME, build_and_deploy
from .helpers import build_connection_string
from .test_new_relations import DATA_INTEGRATOR_APP_NAME

logger = logging.getLogger(__name__)

APPLICATION_APP_NAME = "postgresql-test-app"
APP_NAMES = [DATABASE_APP_NAME, DATA_INTEGRATOR_APP_NAME]
FIRST_DATABASE_RELATION_NAME = "first-database"


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_relations(ops_test: OpsTest, database_charm):
    """Test that check relation data."""
    async with ops_test.fast_forward():
        await build_and_deploy(ops_test, 1, DATABASE_APP_NAME)

        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=3000)

        # Creating first time relation with user role
        await ops_test.model.deploy(DATA_INTEGRATOR_APP_NAME)
        await ops_test.model.applications[DATA_INTEGRATOR_APP_NAME].set_config({
            "database-name": DATA_INTEGRATOR_APP_NAME.replace("-", "_"),
        })
        await ops_test.model.wait_for_idle(apps=[DATA_INTEGRATOR_APP_NAME], status="blocked")
        await ops_test.model.add_relation(DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME)
        await ops_test.model.wait_for_idle(apps=APP_NAMES, status="active")

        connection_string = await build_connection_string(
            ops_test,
            DATA_INTEGRATOR_APP_NAME,
            "postgresql",
            database=DATA_INTEGRATOR_APP_NAME.replace("-", "_"),
        )

        connection = psycopg2.connect(connection_string)
        connection.autocommit = True
        cursor = connection.cursor()
        try:
            random_name = (
                f"test_{''.join(secrets.choice(string.ascii_lowercase) for _ in range(10))}"
            )
            cursor.execute(f"CREATE DATABASE {random_name};")
            assert False, "user role was able to create database"
        except psycopg2.errors.InsufficientPrivilege:
            pass
        finally:
            connection.close()

        with psycopg2.connect(connection_string) as connection:
            connection.autocommit = True
            with connection.cursor() as cursor:
                # Check that it's possible to write and read data from the database that
                # was created for the application.
                cursor.execute("DROP TABLE IF EXISTS test;")
                cursor.execute("CREATE TABLE test(data TEXT);")
                cursor.execute("INSERT INTO test(data) VALUES('some data');")
                cursor.execute("SELECT data FROM test;")
                data = cursor.fetchone()
                assert data[0] == "some data"
        connection.close()

        await ops_test.model.applications[DATABASE_APP_NAME].remove_relation(
            f"{DATABASE_APP_NAME}:database", f"{DATA_INTEGRATOR_APP_NAME}:postgresql"
        )

        # Re-relation with admin role with checking permission
        await ops_test.model.applications[DATA_INTEGRATOR_APP_NAME].set_config({
            "database-name": DATA_INTEGRATOR_APP_NAME.replace("-", "_"),
            "extra-user-roles": "admin",
        })
        await ops_test.model.wait_for_idle(apps=[DATA_INTEGRATOR_APP_NAME], status="blocked")
        await ops_test.model.add_relation(DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME)
        await ops_test.model.wait_for_idle(apps=APP_NAMES, status="active")

        connection_string = await build_connection_string(
            ops_test,
            DATA_INTEGRATOR_APP_NAME,
            "postgresql",
            database=DATA_INTEGRATOR_APP_NAME.replace("-", "_"),
        )
        try:
            connection = psycopg2.connect(connection_string)
            connection.autocommit = True
            cursor = connection.cursor()
            random_name = (
                f"test_{''.join(secrets.choice(string.ascii_lowercase) for _ in range(10))}"
            )
            cursor.execute(f"CREATE DATABASE {random_name};")
        except psycopg2.errors.InsufficientPrivilege:
            assert (
                False
            ), f"failed connect to {random_name} or run a statement in the following database"
        finally:
            connection.close()

        await ops_test.model.applications[DATABASE_APP_NAME].remove_relation(
            f"{DATABASE_APP_NAME}:database", f"{DATA_INTEGRATOR_APP_NAME}:postgresql"
        )

        # Re-relation again with user role and checking write data
        await ops_test.model.applications[DATA_INTEGRATOR_APP_NAME].set_config({
            "database-name": DATA_INTEGRATOR_APP_NAME.replace("-", "_"),
            "extra-user-roles": "",
        })
        await ops_test.model.wait_for_idle(apps=[DATA_INTEGRATOR_APP_NAME], status="blocked")
        await ops_test.model.add_relation(DATA_INTEGRATOR_APP_NAME, DATABASE_APP_NAME)
        await ops_test.model.wait_for_idle(apps=APP_NAMES, status="active")

        for database in [
            DATA_INTEGRATOR_APP_NAME.replace("-", "_"),
            "postgres",
        ]:
            logger.info(f"connecting to the following database: {database}")
            connection_string = await build_connection_string(
                ops_test, DATA_INTEGRATOR_APP_NAME, "postgresql", database=database
            )
            connection = None
            should_fail = database == "postgres"
            try:
                with psycopg2.connect(
                    connection_string
                ) as connection, connection.cursor() as cursor:
                    cursor.execute("SELECT data FROM test;")
                    data = cursor.fetchone()
                    assert data[0] == "some data"

                    # Write some data (it should fail in the "postgres" database).
                    random_name = f"test_{''.join(secrets.choice(string.ascii_lowercase) for _ in range(10))}"
                    cursor.execute(f"CREATE TABLE {random_name}(data TEXT);")
                    if should_fail:
                        assert (
                            False
                        ), f"failed to run a statement in the following database: {database}"
            except psycopg2.errors.InsufficientPrivilege as e:
                if not should_fail:
                    logger.exception(e)
                    assert False, f"failed to connect to or run a statement in the following database: {database}"
            except psycopg2.OperationalError as e:
                if not should_fail:
                    logger.exception(e)
            finally:
                if connection is not None:
                    connection.close()
