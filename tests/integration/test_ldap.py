#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import hashlib
import logging
import uuid
from pathlib import Path

import psycopg2
import pytest
from pytest_operator.plugin import OpsTest
from tenacity import AsyncRetrying, stop_after_attempt, wait_fixed

from . import markers
from .helpers import (
    DATABASE_APP_NAME,
    build_and_deploy,
    execute_query_on_unit,
    get_password,
    get_unit_address,
)

logger = logging.getLogger(__name__)

GLAUTH_PSQL_APP_NAME = "postgresql-k8s"
GLAUTH_CERT_APP_NAME = "self-signed-certificates"
GLAUTH_APP_NAME = "glauth-k8s"
GLAUTH_UTILS_APP_NAME = "glauth-utils"
LDAP_GROUP = "superheros"
LDAP_USER = "jdoe"
LDAP_USER_PASSWORD = "ldap-sync-test"


@pytest.mark.abort_on_fail
@markers.juju3
async def test_build_and_deploy(ops_test: OpsTest, charm) -> None:
    """Build and deploy three units of PostgreSQL."""
    await build_and_deploy(ops_test, charm, num_units=1, wait_for_idle=True)


@pytest.mark.abort_on_fail
@markers.juju3
async def test_glauth_integration(ops_test: OpsTest):
    glauth_psql_app_name = f"glauth-{GLAUTH_PSQL_APP_NAME}"
    glauth_cert_app_name = f"glauth-{GLAUTH_CERT_APP_NAME}"

    # Deploy GLAuth charm
    await asyncio.gather(
        ops_test.model.deploy(
            GLAUTH_PSQL_APP_NAME,
            application_name=glauth_psql_app_name,
            channel="14/stable",
            trust=True,
        ),
        ops_test.model.deploy(
            GLAUTH_CERT_APP_NAME,
            application_name=glauth_cert_app_name,
            channel="1/stable",
            trust=False,
        ),
        ops_test.model.deploy(
            GLAUTH_APP_NAME,
            application_name=GLAUTH_APP_NAME,
            channel="latest/edge",
            trust=True,
        ),
    )

    async with ops_test.fast_forward():
        await asyncio.gather(
            ops_test.model.wait_for_idle(apps=[glauth_psql_app_name], status="active"),
            ops_test.model.wait_for_idle(apps=[glauth_cert_app_name], status="active"),
            ops_test.model.wait_for_idle(apps=[GLAUTH_APP_NAME], status="blocked"),
        )

        # Add both relations to GLAuth (PostgreSQL and self-signed-certificates)
        logger.info("Adding relations to GLAuth")
        await asyncio.gather(
            ops_test.model.add_relation(
                f"{GLAUTH_APP_NAME}:pg-database", f"{glauth_psql_app_name}:database"
            ),
            ops_test.model.add_relation(GLAUTH_APP_NAME, glauth_cert_app_name),
        )
        await asyncio.gather(
            ops_test.model.wait_for_idle(apps=[glauth_psql_app_name], status="active"),
            ops_test.model.wait_for_idle(apps=[glauth_cert_app_name], status="active"),
            ops_test.model.wait_for_idle(apps=[GLAUTH_APP_NAME], status="active"),
        )

        # Add relation to PostgreSQL
        logger.info("Adding relation to PostgreSQL")
        await ops_test.model.add_relation(
            f"{GLAUTH_APP_NAME}:ldap",
            f"{DATABASE_APP_NAME}:ldap",
        )
        await ops_test.model.add_relation(
            f"{GLAUTH_APP_NAME}:send-ca-cert",
            f"{DATABASE_APP_NAME}:receive-ca-cert",
        )

        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active")

        database_units = ops_test.model.applications[DATABASE_APP_NAME].units
        address = await get_unit_address(ops_test, database_units[0].name)
        password = await get_password(ops_test)

        # Validate the 'operator' user can still access the instance
        await execute_query_on_unit(address, password, "SELECT VERSION();")

        # --- LDAP user end-to-end flow ---
        # Map the LDAP group to a PostgreSQL group (the ldap-sync sidecar creates
        # the mapped users and grants them identity_access so they match the hba
        # 'ldap' line), pre-create the mapped role the group grants into, and
        # create the user in glauth through the glauth-utils charm.
        logger.info("Creating the mapped PostgreSQL group and setting the LDAP group mapping")
        # The charm creates identity_access (NOLOGIN) but does not grant it
        # CONNECT on the postgres database yet; LDAP users can only pass the
        # hba 'ldap' line into a database if the group has CONNECT. Pending the
        # charm-side grant, do it here so the auth poll can complete.
        await execute_query_on_unit(
            address,
            password,
            f'CREATE ROLE "{LDAP_GROUP}" NOLOGIN; '
            'GRANT CONNECT ON DATABASE postgres TO "identity_access"; SELECT 1;',
        )
        await ops_test.model.applications[DATABASE_APP_NAME].set_config({
            "ldap-map": f"{LDAP_GROUP}={LDAP_GROUP}"
        })

        logger.info("Deploying the glauth-utils charm and creating the LDAP user")
        await ops_test.model.deploy(
            GLAUTH_UTILS_APP_NAME,
            channel="edge",
            trust=True,
        )
        await ops_test.model.integrate(GLAUTH_UTILS_APP_NAME, GLAUTH_APP_NAME)
        await ops_test.model.wait_for_idle(
            apps=[GLAUTH_UTILS_APP_NAME], status="active", timeout=10 * 60
        )

        # glauth-utils' apply-ldif action reads the file from its own container.
        # GLAuth compares sha256(plaintext) HEX digests, not base64.
        password_hash = "{SHA256}" + hashlib.sha256(LDAP_USER_PASSWORD.encode()).hexdigest()
        ldif = (
            f"dn: ou={LDAP_GROUP},dc=glauth,dc=com\n"
            "objectClass: posixGroup\n"
            f"ou: {LDAP_GROUP}\n"
            "gidNumber: 5502\n"
            f"\ndn: cn={LDAP_USER},ou={LDAP_GROUP},dc=glauth,dc=com\n"
            "changetype: add\n"
            "objectClass: posixAccount\n"
            "uidNumber: 5002\n"
            "gidNumber: 5502\n"
            f"cn: {LDAP_USER}\n"
            "sn: doe\n"
            f"uid: {LDAP_USER}\n"
            f"userPassword: {password_hash}\n"
        )
        # The juju snap cannot read /tmp or /var/tmp (private namespace), and
        # a transfer sourced from there lands as an empty file on the unit.
        # $HOME is visible to the snap; the unique name avoids colliding with
        # a stale root-owned copy left by a previous run (juju scp cannot
        # overwrite it as the charm user).
        ldif_path = Path.home() / f"ldap-test-{uuid.uuid4().hex[:8]}.ldif"
        ldif_path.write_text(ldif)
        unit_ldif_path = f"/var/tmp/{ldif_path.name}"
        await ops_test.juju("scp", str(ldif_path), f"{GLAUTH_UTILS_APP_NAME}/0:{unit_ldif_path}")
        action = (
            await ops_test.model
            .applications[GLAUTH_UTILS_APP_NAME]
            .units[0]
            .run_action("apply-ldif", path=unit_ldif_path)
        )
        await action.wait()
        assert action.results["return-code"] == 0

        # The ldap-sync sidecar runs every 30s; poll until the role materialises,
        # then authenticate AS the LDAP user through the hba 'ldap' line.
        # Diagnostic for the CI artifacts: is the sidecar service up, and did the
        # role land? pebble logs are not captured elsewhere.
        services = await ops_test.juju(
            "exec", "--unit", f"{DATABASE_APP_NAME}/0", "--", "pebble", "services"
        )
        logger.info("ldap-sync pebble services:\n%s", services)
        roles = await execute_query_on_unit(
            address, password, "SELECT rolname FROM pg_roles WHERE rolname LIKE '%doe%'"
        )
        logger.info("synced roles so far: %s", roles)
        logger.info("Waiting for the LDAP user to sync into PostgreSQL and authenticating")
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(12), wait=wait_fixed(30), reraise=True
        ):
            with attempt:
                await _execute_query_as(address, LDAP_USER, LDAP_USER_PASSWORD, "SELECT 1;")


def _execute_query_as(address: str, user: str, password: str, query: str) -> list:
    """Execute a query connecting as the given user (not the charm operator)."""
    with (
        psycopg2.connect(
            f"dbname='postgres' user='{user}' host='{address}'"
            f"password='{password}' connect_timeout=10"
        ) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(query)
        return list(cursor.fetchall())
