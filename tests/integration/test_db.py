#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
from asyncio import gather

import pytest as pytest
from pytest_operator.plugin import OpsTest

from tests.integration.helpers import (
    DATABASE_APP_NAME,
    build_and_deploy,
    check_database_creation,
    check_database_users_existence,
    deploy_and_relate_application_with_postgresql,
)

FINOS_WALTZ_APP_NAME = "finos-waltz"
ANOTHER_FINOS_WALTZ_APP_NAME = "another-finos-waltz"
APPLICATION_UNITS = 1
DATABASE_UNITS = 3

charm = None


async def build_and_deploy(ops_test: OpsTest, num_units: int) -> None:
    """Builds the charm and deploys a specified number of units."""
    global charm
    if not charm:
        charm = await ops_test.build_charm(".")
    resources = {
        "postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"],
    }
    await ops_test.model.deploy(
        charm,
        resources=resources,
        application_name=DATABASE_APP_NAME,
        trust=True,
        num_units=num_units,
    ),
    # Wait until the PostgreSQL charm is successfully deployed.
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME],
        status="active",
        raise_on_blocked=True,
        timeout=1000,
        wait_for_exact_units=num_units,
    )


@pytest.mark.db_relation_tests
async def test_finos_waltz_db(ops_test: OpsTest) -> None:
    """Deploy Finos Waltz to test the 'db' relation.

    Args:
        ops_test: The ops test framework
    """
    async with ops_test.fast_forward():
        # Build and deploy the PostgreSQL charm.
        await build_and_deploy(ops_test, DATABASE_UNITS)

        assert len(ops_test.model.applications[DATABASE_APP_NAME].units) == DATABASE_UNITS

        for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
            assert unit.workload_status == "active"

        # Deploy and test the first deployment of Finos Waltz.
        relation_id = await deploy_and_relate_application_with_postgresql(
            ops_test, "finos-waltz-k8s", FINOS_WALTZ_APP_NAME, APPLICATION_UNITS, channel="edge"
        )
        await check_database_creation(ops_test, "waltz")

        finos_waltz_users = [f"relation_id_{relation_id}"]

        await check_database_users_existence(ops_test, finos_waltz_users, [])

        # Deploy and test another deployment of Finos Waltz.
        another_relation_id = await deploy_and_relate_application_with_postgresql(
            ops_test,
            "finos-waltz-k8s",
            ANOTHER_FINOS_WALTZ_APP_NAME,
            APPLICATION_UNITS,
            channel="edge",
        )
        # In this case, the database name is the same as in the first deployment
        # because it's a fixed value in Finos Waltz charm.
        await check_database_creation(ops_test, "waltz")

        another_finos_waltz_users = [f"relation_id_{another_relation_id}"]

        await check_database_users_existence(
            ops_test, finos_waltz_users + another_finos_waltz_users, []
        )

        # Scale down the second deployment of Finos Waltz and confirm that the first deployment
        # is still active.
        await ops_test.model.remove_application(
            ANOTHER_FINOS_WALTZ_APP_NAME, block_until_done=True
        )

        another_finos_waltz_users = []
        await check_database_users_existence(
            ops_test, finos_waltz_users, another_finos_waltz_users
        )

        # Remove the first deployment of Finos Waltz.
        await ops_test.model.remove_application(FINOS_WALTZ_APP_NAME, block_until_done=True)

        # Remove the PostgreSQL application.
        await ops_test.model.remove_application(DATABASE_APP_NAME, block_until_done=True)


@pytest.mark.db_relation_tests
async def test_indico_db_blocked(ops_test: OpsTest) -> None:
    """Tests if deploying and relating to Indico charm will block due to requested extensions."""
    async with ops_test.fast_forward():
        # Build and deploy the PostgreSQL charm.
        await build_and_deploy(ops_test, 1)

        await ops_test.model.deploy(
            "indico",
            channel="stable",
            application_name="indico",
            num_units=APPLICATION_UNITS,
        )

        # Wait for model to stabilise
        await ops_test.model.wait_for_idle(
            apps=["indico"],
            status="waiting",
            raise_on_blocked=False,
            timeout=1000,
        )

        await ops_test.model.relate(f"{DATABASE_APP_NAME}:db", "indico:db")

        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME],
            status="blocked",
            raise_on_blocked=False,
            timeout=1000,
        )

        assert (
            ops_test.model.applications[DATABASE_APP_NAME].units[0].workload_status_message
            == "extensions requested through relation"
        )

        # Cleanup
        await gather(
            ops_test.model.remove_application(DATABASE_APP_NAME, block_until_done=True),
            ops_test.model.remove_application("indico", block_until_done=True),
        )
