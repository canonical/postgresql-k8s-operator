#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

from pytest_operator.plugin import OpsTest

from tests.helpers import METADATA
from tests.integration.helpers import (
    DATABASE_APP_NAME,
    TLS_RESOURCES,
    attach_resource,
    check_database_creation,
    check_database_users_existence,
    deploy_and_relate_application_with_postgresql,
)

MATTERMOST_APP_NAME = "mattermost"
APPLICATION_UNITS = 2
DATABASE_UNITS = 3


async def test_mattermost_db(ops_test: OpsTest) -> None:
    """Deploy Mattermost to test the 'db' relation.

    Mattermost needs TLS enabled on PostgreSQL to correctly connect to it.

    Args:
        ops_test: The ops test framework
    """
    async with ops_test.fast_forward():
        # Build and deploy the PostgreSQL charm.
        charm = await ops_test.build_charm(".")
        resources = {
            "postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"],
            "cert-file": METADATA["resources"]["cert-file"]["filename"],
            "key-file": METADATA["resources"]["key-file"]["filename"],
        }
        await ops_test.model.deploy(
            charm,
            resources=resources,
            application_name=DATABASE_APP_NAME,
            trust=True,
            num_units=DATABASE_UNITS,
        ),
        # Wait until the PostgreSQL charm is successfully deployed.
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME],
            status="active",
            raise_on_blocked=True,
            timeout=1000,
            wait_for_exact_units=DATABASE_UNITS,
        )
        assert len(ops_test.model.applications[DATABASE_APP_NAME].units) == DATABASE_UNITS

        # Add TLS certificate and key to PostgreSQL.
        for rsc_name, src_path in TLS_RESOURCES.items():
            await attach_resource(ops_test, DATABASE_APP_NAME, rsc_name, src_path)

        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME],
            status="active",
            raise_on_blocked=False,  # The charm can be blocked when PostgreSQL is not ready yet.
            timeout=1000,
            wait_for_exact_units=DATABASE_UNITS,
        )

        # for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
        #     assert unit.workload_status == "active"
        #
        # # Deploy and test Mattermost.
        # relation_id = await deploy_and_relate_application_with_postgresql(
        #     ops_test, "mattermost-k8s", MATTERMOST_APP_NAME, APPLICATION_UNITS, status="waiting"
        # )
        # await check_database_creation(ops_test, "mattermost")
        #
        # mattermost_users = [f"relation_id_{relation_id}"]
        #
        # await check_database_users_existence(ops_test, mattermost_users, [])
        #
        # # Remove the deployment of Mattermost.
        # await ops_test.model.remove_application(MATTERMOST_APP_NAME, block_until_done=True)
        #
        # # Remove the PostgreSQL application.
        # await ops_test.model.remove_application(DATABASE_APP_NAME, block_until_done=True)
