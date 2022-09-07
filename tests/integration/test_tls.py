#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import pytest as pytest
from pytest_operator.plugin import OpsTest

from tests.helpers import METADATA
from tests.integration.helpers import (
    DATABASE_APP_NAME,
    check_database_creation,
    check_database_users_existence,
    deploy_and_relate_application_with_postgresql,
    is_tls_enabled,
)

MATTERMOST_APP_NAME = "mattermost"
TLS_CERTIFICATES_APP_NAME = "tls-certificates-operator"
APPLICATION_UNITS = 2
DATABASE_UNITS = 3


@pytest.mark.abort_on_fail
@pytest.mark.tls_tests
@pytest.mark.skip_if_deployed
async def test_deploy_active(ops_test: OpsTest):
    """Build the charm and deploy it."""
    charm = await ops_test.build_charm(".")
    async with ops_test.fast_forward():
        await ops_test.model.deploy(
            charm,
            resources={
                "postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"]
            },
            application_name=DATABASE_APP_NAME,
            num_units=DATABASE_UNITS,
            trust=True,
        )
        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1000)


@pytest.mark.tls_tests
async def test_mattermost_db(ops_test: OpsTest) -> None:
    """Deploy Mattermost to test the 'db' relation.

    Mattermost needs TLS enabled on PostgreSQL to correctly connect to it.

    Args:
        ops_test: The ops test framework
    """
    async with ops_test.fast_forward():
        # Deploy TLS Certificates operator.
        config = {"generate-self-signed-certificates": "true", "ca-common-name": "Test CA"}
        await ops_test.model.deploy(TLS_CERTIFICATES_APP_NAME, channel="edge", config=config)
        await ops_test.model.wait_for_idle(
            apps=[TLS_CERTIFICATES_APP_NAME], status="active", timeout=1000
        )

        # Relate it to the PostgreSQL to enable TLS.
        await ops_test.model.relate(DATABASE_APP_NAME, TLS_CERTIFICATES_APP_NAME)
        await ops_test.model.wait_for_idle(status="active", timeout=1000)

        # Wait for all units enabling TLS.
        for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
            assert await is_tls_enabled(ops_test, unit.name)

        # Deploy and check Mattermost user and database existence.
        relation_id = await deploy_and_relate_application_with_postgresql(
            ops_test, "mattermost-k8s", MATTERMOST_APP_NAME, APPLICATION_UNITS, status="waiting"
        )
        await check_database_creation(ops_test, "mattermost")

        mattermost_users = [f"relation_id_{relation_id}"]

        await check_database_users_existence(ops_test, mattermost_users, [])
