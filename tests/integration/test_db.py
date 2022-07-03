#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

from pytest_operator.plugin import OpsTest

from tests.helpers import METADATA
from tests.integration.helpers import (
    DATABASE_APP_NAME,
    check_database_creation,
    check_database_users_existence,
    deploy_and_relate_application_with_postgresql,
)

logger = logging.getLogger(__name__)

FINOS_WALTZ_APP_NAME = "finos-waltz"
ANOTHER_FINOS_WALTZ_APP_NAME = "another-finos-waltz"


async def test_finos_waltz_db(ops_test: OpsTest) -> None:
    """Deploy Finos Waltz to test the 'db' relation.

    Args:
        ops_test: The ops test framework
    """
    # Build and deploy the PostgreSQL charm.
    charm = await ops_test.build_charm(".")
    resources = {
        "postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"],
    }
    await ops_test.model.deploy(
        charm, resources=resources, application_name=DATABASE_APP_NAME, trust=True  # , num_units=3
    ),
    # Wait until the PostgreSQL charm is successfully deployed.
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME],
        status="active",
        raise_on_blocked=True,
        timeout=1000,
        # wait_for_exact_units=3,
    )
    # assert len(ops_test.model.applications[DATABASE_APP_NAME].units) == 3

    for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
        assert unit.workload_status == "active"

    # Deploy and test the first deployment of Finos Waltz.
    relation_id = await deploy_and_relate_application_with_postgresql(
        ops_test, "finos-waltz-k8s", FINOS_WALTZ_APP_NAME, 1, channel="edge"
    )
    await check_database_creation(ops_test, "waltz")

    finos_waltz_users = [f"relation_id_{relation_id}"]

    await check_database_users_existence(ops_test, finos_waltz_users, [])

    # Deploy and test another deployment of Finos Waltz.
    another_relation_id = await deploy_and_relate_application_with_postgresql(
        ops_test, "finos-waltz-k8s", ANOTHER_FINOS_WALTZ_APP_NAME, 1, channel="edge"
    )
    await check_database_creation(ops_test, "waltz")

    another_finos_waltz_users = [f"relation_id_{another_relation_id}"]

    await check_database_users_existence(
        ops_test, finos_waltz_users + another_finos_waltz_users, []
    )

    # Scale down the second deployment of Finos Waltz and confirm that the first deployment
    # is still active.
    await ops_test.model.remove_application(ANOTHER_FINOS_WALTZ_APP_NAME, block_until_done=True)

    another_finos_waltz_users = []
    await check_database_users_existence(ops_test, finos_waltz_users, another_finos_waltz_users)

    # Remove the first deployment of Finos Waltz.
    await ops_test.model.remove_application(FINOS_WALTZ_APP_NAME, block_until_done=True)

    # Remove the PostgreSQL application.
    await ops_test.model.remove_application(DATABASE_APP_NAME, block_until_done=True)
