#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import pytest as pytest
import requests
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from . import markers
from .ha_tests.helpers import (
    change_patroni_setting,
)
from .helpers import (
    DATABASE_APP_NAME,
    build_and_deploy,
    check_database_creation,
    check_database_users_existence,
    check_tls,
    check_tls_patroni_api,
    check_tls_replication,
    db_connect,
    deploy_and_relate_application_with_postgresql,
    get_password,
    get_primary,
    get_unit_address,
    primary_changed,
    run_command_on_unit,
)
from .juju_ import juju_major_version

logger = logging.getLogger(__name__)

MATTERMOST_APP_NAME = "mattermost"
if juju_major_version < 3:
    tls_certificates_app_name = "tls-certificates-operator"
    tls_channel = "legacy/stable"
    tls_base = "ubuntu@22.04"
    tls_config = {"generate-self-signed-certificates": "true", "ca-common-name": "Test CA"}
else:
    tls_certificates_app_name = "self-signed-certificates"
    tls_channel = "1/stable"
    tls_base = "ubuntu@24.04"
    tls_config = {"ca-common-name": "Test CA"}
APPLICATION_UNITS = 2
DATABASE_UNITS = 3


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest, charm) -> None:
    """Build and deploy three units of PostgreSQL."""
    await build_and_deploy(ops_test, charm, DATABASE_UNITS, wait_for_idle=False)


async def check_tls_rewind(ops_test: OpsTest) -> None:
    """Checks if TLS was used by rewind."""
    for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
        logger.info(f"checking if pg_rewind used TLS on {unit.name}")
        try:
            logs = await run_command_on_unit(
                ops_test,
                unit.name,
                "grep rewind /var/log/postgresql/postgresql-*.log",
            )
        except Exception:
            continue
        if "connection authorized: user=rewind database=postgres SSL enabled" in logs:
            break
    assert "connection authorized: user=rewind database=postgres SSL enabled" in logs, (
        "TLS is not being used on pg_rewind connections"
    )


@pytest.mark.abort_on_fail
async def test_tls(ops_test: OpsTest) -> None:
    async with ops_test.fast_forward():
        # Deploy TLS Certificates operator.
        await ops_test.model.deploy(
            tls_certificates_app_name, config=tls_config, channel=tls_channel, base=tls_base
        )
        # Relate it to the PostgreSQL to enable TLS.
        await ops_test.model.relate(
            f"{DATABASE_APP_NAME}:certificates", f"{tls_certificates_app_name}:certificates"
        )
        await ops_test.model.wait_for_idle(status="active", timeout=1000, raise_on_error=False)

        # Wait for all units enabling TLS.
        for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
            assert await check_tls(ops_test, unit.name, enabled=True)
            assert await check_tls_patroni_api(ops_test, unit.name, enabled=True)

        # Test TLS being used by pg_rewind. To accomplish that, get the primary unit
        # and a replica that will be promoted to primary (this should trigger a rewind
        # operation when the old primary is started again). 'verify=False' is used here
        # because the unit IP that is used in the test doesn't match the certificate
        # hostname (that is a k8s hostname).
        primary = await get_primary(ops_test)
        primary_address = await get_unit_address(ops_test, primary)
        patroni_password = await get_password(ops_test, "patroni")
        cluster_info = requests.get(f"https://{primary_address}:8008/cluster", verify=False)
        for member in cluster_info.json()["members"]:
            if member["role"] != "leader":
                replica = "/".join(member["name"].rsplit("-", 1))

        # Check if TLS enabled for replication
        assert await check_tls_replication(ops_test, primary, enabled=True)

        # Enable additional logs on the PostgreSQL instance to check TLS
        # being used in a later step.
        await ops_test.model.applications[DATABASE_APP_NAME].set_config({
            "logging_log_connections": "True"
        })
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME], status="active", idle_period=30
        )
        # Pause Patroni so it doesn't wipe the custom changes
        await change_patroni_setting(ops_test, "pause", True, patroni_password, tls=True)

    async with ops_test.fast_forward("24h"):
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(2), reraise=True):
            with attempt:
                # Promote the replica to primary.
                await run_command_on_unit(
                    ops_test,
                    replica,
                    'su postgres -c "/usr/lib/postgresql/14/bin/pg_ctl -D /var/lib/postgresql/data/pgdata promote"',
                )

                # Check that the replica was promoted.
                host = await get_unit_address(ops_test, replica)
                password = await get_password(ops_test)
                with db_connect(host, password) as connection, connection.cursor() as cursor:
                    cursor.execute("SELECT pg_is_in_recovery();")
                    in_recovery = cursor.fetchone()[0]
                    assert not in_recovery  # If the instance is not in recovery mode anymore it was successfully promoted.
                connection.close()

        # Write some data to the initial primary (this causes a divergence
        # in the instances' timelines).
        host = await get_unit_address(ops_test, primary)
        password = await get_password(ops_test)
        patroni_password = await get_password(ops_test, "patroni")
        with db_connect(host, password) as connection:
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute("CREATE TABLE pgrewindtest (testcol INT);")
                cursor.execute("INSERT INTO pgrewindtest SELECT generate_series(1,1000);")
        connection.close()

        # Stop the initial primary.
        logger.info(f"stopping database on {primary}")
        try:
            await run_command_on_unit(ops_test, primary, "/charm/bin/pebble stop postgresql")
        except Exception as e:
            # pebble stop on juju 2 errors out and leaves dangling PG processes
            if juju_major_version > 2:
                raise e
            await run_command_on_unit(ops_test, primary, "pkill --signal SIGTERM -x postgres")

        # Check that the primary changed.
        assert await primary_changed(ops_test, primary), "primary not changed"

        # Check the logs to ensure TLS is being used by pg_rewind.
        for attempt in Retrying(stop=stop_after_delay(60 * 3), wait=wait_fixed(2), reraise=True):
            with attempt:
                await check_tls_rewind(ops_test)
        await change_patroni_setting(ops_test, "pause", False, patroni_password, tls=True)

    async with ops_test.fast_forward():
        # Await for postgresql to be stable if not already
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME], status="active", idle_period=15
        )


@markers.amd64_only  # mattermost-k8s charm not available for arm64
async def test_mattermost_db(ops_test: OpsTest) -> None:
    """Deploy Mattermost to test the 'db' relation.

    Mattermost needs TLS enabled on PostgreSQL to correctly connect to it.

    Args:
        ops_test: The ops test framework
    """
    async with ops_test.fast_forward():
        # Deploy and check Mattermost user and database existence.
        relation_id = await deploy_and_relate_application_with_postgresql(
            ops_test,
            "mattermost-k8s",
            MATTERMOST_APP_NAME,
            APPLICATION_UNITS,
            status="waiting",
            base="ubuntu@20.04",
        )
        await check_database_creation(ops_test, "mattermost")

        mattermost_users = [f"relation_id_{relation_id}"]

        await check_database_users_existence(ops_test, mattermost_users, [])


async def test_remove_tls(ops_test: OpsTest) -> None:
    async with ops_test.fast_forward():
        # Remove the relation.
        await ops_test.model.applications[DATABASE_APP_NAME].remove_relation(
            f"{DATABASE_APP_NAME}:certificates", f"{tls_certificates_app_name}:certificates"
        )
        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1000)

        # Wait for all units disabling TLS.
        for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
            assert await check_tls(ops_test, unit.name, enabled=False)
            assert await check_tls_patroni_api(ops_test, unit.name, enabled=False)
