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
    check_tls,
    check_tls_patroni_api,
    deploy_and_relate_application_with_postgresql,
    enable_connections_logging,
    get_primary,
    primary_changed,
    run_command_on_unit,
)

MATTERMOST_APP_NAME = "mattermost"
TLS_CERTIFICATES_APP_NAME = "tls-certificates-operator"
APPLICATION_UNITS = 2
DATABASE_UNITS = 3


@pytest.mark.tls_tests
async def test_mattermost_db(ops_test: OpsTest) -> None:
    """Deploy Mattermost to test the 'db' relation.

    Mattermost needs TLS enabled on PostgreSQL to correctly connect to it.

    Args:
        ops_test: The ops test framework
    """
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
        # Deploy TLS Certificates operator.
        config = {"generate-self-signed-certificates": "true", "ca-common-name": "Test CA"}
        await ops_test.model.deploy(TLS_CERTIFICATES_APP_NAME, channel="edge", config=config)
        # Relate it to the PostgreSQL to enable TLS.
        await ops_test.model.relate(DATABASE_APP_NAME, TLS_CERTIFICATES_APP_NAME)
        await ops_test.model.wait_for_idle(status="active", timeout=1000)

        # Wait for all units enabling TLS.
        for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
            assert await check_tls(ops_test, unit.name, enabled=True)
            assert await check_tls_patroni_api(ops_test, unit.name, enabled=True)

        # Test TLS being used by pg_rewind. To accomplish that, get the primary unit
        # and a replica that will be promoted to primary (this should trigger a rewind
        # operation when the old primary is started again).
        primary = await get_primary(ops_test)
        replica = [
            unit.name
            for unit in ops_test.model.applications[DATABASE_APP_NAME].units
            if unit.name != primary
        ][0]

        # Enable additional logs on the PostgreSQL instance to check TLS
        # being used in a later step.
        await enable_connections_logging(ops_test, primary)

        # Promote the replica to primary.
        await run_command_on_unit(
            ops_test,
            replica,
            'su postgres -c "/usr/lib/postgresql/14/bin/pg_ctl -D /var/lib/postgresql/data/pgdata promote"',
        )

        # Stop the initial primary.
        await run_command_on_unit(ops_test, primary, "/charm/bin/pebble stop postgresql")

        # Check that the primary changed.
        assert await primary_changed(ops_test, primary), "primary not changed"

        # Restart the initial primary and check the logs to ensure TLS is being used by pg_rewind.
        await run_command_on_unit(ops_test, primary, "/charm/bin/pebble start postgresql")
        logs = await run_command_on_unit(ops_test, replica, "/charm/bin/pebble logs")
        assert (
            "connection authorized: user=rewind database=postgres"
            " SSL enabled (protocol=TLSv1.3, cipher=TLS_AES_256_GCM_SHA384, bits=256)" in logs
        ), "TLS is not being used on pg_rewind connections"

        # Deploy and check Mattermost user and database existence.
        relation_id = await deploy_and_relate_application_with_postgresql(
            ops_test, "mattermost-k8s", MATTERMOST_APP_NAME, APPLICATION_UNITS, status="waiting"
        )
        await check_database_creation(ops_test, "mattermost")

        mattermost_users = [f"relation_id_{relation_id}"]

        await check_database_users_existence(ops_test, mattermost_users, [])

        # Remove the relation.
        await ops_test.model.applications[DATABASE_APP_NAME].remove_relation(
            f"{DATABASE_APP_NAME}:certificates", f"{TLS_CERTIFICATES_APP_NAME}:certificates"
        )
        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1000)

        # Wait for all units disabling TLS.
        for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
            assert await check_tls(ops_test, unit.name, enabled=False)
            assert await check_tls_patroni_api(ops_test, unit.name, enabled=False)
