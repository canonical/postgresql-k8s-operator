#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import json
import time

import psycopg2
import pytest
from pytest_operator.plugin import OpsTest

from . import markers
from .helpers import (
    CHARM_SERIES,
    METADATA,
    check_patroni,
    db_connect,
    get_leader_unit,
    get_password,
    get_primary,
    get_unit_address,
    restart_patroni,
    set_password,
)

APP_NAME = METADATA["name"]


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
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
            application_name=APP_NAME,
            num_units=3,
            series=CHARM_SERIES,
            trust=True,
            config={"profile": "testing"},
        )
        await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)


@pytest.mark.group(1)
async def test_password_rotation(ops_test: OpsTest):
    """Test password rotation action."""
    # Get the initial passwords set for the system users.
    superuser_password = await get_password(ops_test)
    replication_password = await get_password(ops_test, "replication")
    monitoring_password = await get_password(ops_test, "monitoring")
    backup_password = await get_password(ops_test, "backup")
    rewind_password = await get_password(ops_test, "rewind")

    # Get the leader unit name (because passwords can only be set through it).
    leader_unit = await get_leader_unit(ops_test, APP_NAME)
    leader = leader_unit.name

    # Change both passwords.
    result = await set_password(ops_test, unit_name=leader)
    assert "password" in result.keys()
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    # For replication, generate a specific password and pass it to the action.
    new_replication_password = "test-password"
    result = await set_password(
        ops_test, unit_name=leader, username="replication", password=new_replication_password
    )
    assert "password" in result.keys()
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    # For monitoring, generate a specific password and pass it to the action.
    new_monitoring_password = "test-password"
    result = await set_password(
        ops_test, unit_name=leader, username="monitoring", password=new_monitoring_password
    )
    assert "password" in result.keys()
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    # For backup, generate a specific password and pass it to the action.
    new_backup_password = "test-password"
    result = await set_password(
        ops_test, unit_name=leader, username="backup", password=new_backup_password
    )
    assert "password" in result.keys()
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    # For rewind, generate a specific password and pass it to the action.
    new_rewind_password = "test-password"
    result = await set_password(
        ops_test, unit_name=leader, username="rewind", password=new_rewind_password
    )
    assert "password" in result.keys()
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    new_superuser_password = await get_password(ops_test)
    assert superuser_password != new_superuser_password
    assert new_replication_password == await get_password(ops_test, "replication")
    assert replication_password != new_replication_password
    assert new_monitoring_password == await get_password(ops_test, "monitoring")
    assert monitoring_password != new_monitoring_password
    assert new_backup_password == await get_password(ops_test, "backup")
    assert backup_password != new_backup_password
    assert new_rewind_password == await get_password(ops_test, "rewind")
    assert rewind_password != new_rewind_password

    # Restart Patroni on any non-leader unit and check that
    # Patroni and PostgreSQL continue to work.
    restart_time = time.time()
    for unit in ops_test.model.applications[APP_NAME].units:
        if not await unit.is_leader_from_status():
            await restart_patroni(ops_test, unit.name)
            assert await check_patroni(ops_test, unit.name, restart_time)


@pytest.mark.group(1)
@markers.juju_secrets
async def test_password_from_secret_same_as_cli(ops_test: OpsTest):
    """Checking if password is same as returned by CLI.

    I.e. we're manipulating the secret we think we're manipulating.
    """
    #
    # No way to retrieve a secet by label for now (https://bugs.launchpad.net/juju/+bug/2037104)
    # Therefore we take advantage of the fact, that we only have ONE single secret a this point
    # So we take the single member of the list
    # NOTE: This would BREAK if for instance units had secrets at the start...
    #
    password = await get_password(ops_test, username="replication")
    complete_command = "list-secrets"
    _, stdout, _ = await ops_test.juju(*complete_command.split())
    secret_id = stdout.split("\n")[1].split(" ")[0]

    # Getting back the pw from juju CLI
    complete_command = f"show-secret {secret_id} --reveal --format=json"
    _, stdout, _ = await ops_test.juju(*complete_command.split())
    data = json.loads(stdout)
    assert data[secret_id]["content"]["Data"]["replication-password"] == password


@pytest.mark.group(1)
async def test_empty_password(ops_test: OpsTest) -> None:
    """Test that the password can't be set to an empty string."""
    leader_unit = await get_leader_unit(ops_test, APP_NAME)
    leader = leader_unit.name
    await set_password(ops_test, unit_name=leader, username="replication", password="")
    password = await get_password(ops_test, unit_name=leader, username="replication")
    # The password is 'None', BUT NOT because of SECRET_DELETED_LABEL
    # `get_secret()` returns a None value (as the field in the secret is set to string value "None")
    # And this true None value is turned to a string when the event is setting results.
    assert password == "None"


@pytest.mark.group(1)
async def test_db_connection_with_empty_password(ops_test: OpsTest):
    """Test that user can't connect with empty password."""
    primary = await get_primary(ops_test)
    address = await get_unit_address(ops_test, primary)
    with pytest.raises(psycopg2.Error):
        with db_connect(host=address, password="") as connection:
            connection.close()


@pytest.mark.group(1)
async def test_no_password_change_on_invalid_password(ops_test: OpsTest) -> None:
    """Test that in general, there is no change when password validation fails."""
    leader_unit = await get_leader_unit(ops_test, APP_NAME)
    leader = leader_unit.name
    password1 = await get_password(ops_test, username="replication")
    # The password has to be minimum 3 characters
    await set_password(ops_test, unit_name=leader, username="replication", password="ca" * 1000000)
    password2 = await get_password(ops_test, username="replication")
    # The password didn't change
    assert password1 == password2
