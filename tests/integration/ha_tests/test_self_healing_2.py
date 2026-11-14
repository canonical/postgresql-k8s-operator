# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import pytest
import requests
from pytest_operator.plugin import OpsTest

from ..helpers import (
    APPLICATION_NAME,
    DATABASE_APP_NAME,
    METADATA,
    app_name,
    build_and_deploy,
    get_password,
    get_unit_address,
    scale_application,
    set_password,
)
from ..new_relations.helpers import build_connection_string
from .helpers import (
    apply_pvc_with_third_party_storage,
    are_writes_increasing,
    check_writes,
    create_test_data,
    fetch_cluster_members,
    is_postgresql_ready,
    restore_own_storage,
    start_continuous_writes,
    validate_test_data,
)

logger = logging.getLogger(__name__)

APP_NAME = METADATA["name"]
PATRONI_PROCESS = "/usr/bin/patroni"
POSTGRESQL_PROCESS = "postgres"
DB_PROCESSES = [POSTGRESQL_PROCESS, PATRONI_PROCESS]
MEDIAN_ELECTION_TIME = 10
THIRD_PARTY_STORAGE_MESSAGE = (
    "Failed to start postgresql. The storage belongs to a third-party cluster"
)


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest, charm) -> None:
    """Build and deploy three unit of PostgreSQL."""
    wait_for_apps = False
    # It is possible for users to provide their own cluster for HA testing. Hence, check if there
    # is a pre-existing cluster.
    if not await app_name(ops_test):
        wait_for_apps = True
        await build_and_deploy(ops_test, charm, 3, wait_for_idle=False)
    # Deploy the continuous writes application charm if it wasn't already deployed.
    if not await app_name(ops_test, APPLICATION_NAME):
        wait_for_apps = True
        async with ops_test.fast_forward():
            await ops_test.model.deploy(
                APPLICATION_NAME,
                application_name=APPLICATION_NAME,
                channel="latest/edge",
                series="jammy",
            )

    if wait_for_apps:
        await ops_test.model.relate(DATABASE_APP_NAME, f"{APPLICATION_NAME}:database")
        await ops_test.model.wait_for_idle(
            apps=[
                APPLICATION_NAME,
                DATABASE_APP_NAME,
            ],
            status="active",
            timeout=1000,
            idle_period=30,
        )


@pytest.mark.abort_on_fail
async def test_scaling_to_zero(ops_test: OpsTest, charm, continuous_writes) -> None:
    """Scale the database to zero units, swap storage, and scale up again."""
    # Locate primary unit.
    app = await app_name(ops_test)
    second_app = f"second-{app}"

    # Deploy a second single-unit cluster for the storage-swap steps.
    await build_and_deploy(ops_test, charm, 1, database_app_name=second_app)

    # Align passwords so the swapped storage stays usable.
    for user in ["operator", "replication", "rewind"]:
        password = await get_password(ops_test, username=user, database_app_name=app)
        second_primary = ops_test.model.applications[second_app].units[0].name
        await set_password(ops_test, second_primary, user, password)

    # Start an application that continuously writes data to the database.
    await start_continuous_writes(ops_test, app)

    # Get the connection string to connect to the database using the read/write endpoint.
    connection_string = await build_connection_string(ops_test, APPLICATION_NAME, "database")

    logger.info("connect to DB and create test table")
    await create_test_data(connection_string)

    primary_name = ops_test.model.applications[app].units[0].name
    secondary_name = ops_test.model.applications[second_app].units[0].name

    unit_ip_addresses = []
    for unit in ops_test.model.applications[app].units:
        # Save IP addresses of units
        unit_ip_addresses.append(await get_unit_address(ops_test, unit.name))

    # Scale the database to zero units.
    logger.info("scaling database to zero units")
    await scale_application(ops_test, app, 0)
    await scale_application(ops_test, second_app, 0)

    logger.info("checking shutdown units")
    for unit_ip in unit_ip_addresses:
        try:
            resp = requests.get(f"http://{unit_ip}:8008")
            assert resp.status_code != 200, (
                f"status code = {resp.status_code}, message = {resp.text}"
            )
        except requests.exceptions.ConnectionError:
            assert True, f"unit host = http://{unit_ip}:8008, all units shutdown"
        except Exception as e:
            assert False, f"{e} unit host = http://{unit_ip}:8008, something went wrong"

    logger.info("apply pvc with third party storage")
    storage = await apply_pvc_with_third_party_storage(
        ops_test,
        secondary_application=second_app,
        primary_unit_name=primary_name,
        secondary_unit_name=secondary_name,
    )

    logger.info("scaling database to one unit")
    await scale_application(ops_test, app, 1, is_blocked=True)

    logger.info("checking blocked status using third-party cluster storage")
    assert any(
        unit.workload_status == "blocked"
        and unit.workload_status_message == THIRD_PARTY_STORAGE_MESSAGE
        for unit in ops_test.model.applications[app].units
    ), "The blocked status check should be checked along with the message"

    logger.info("scaling database to zero unit")
    await scale_application(ops_test, app, 0)

    await restore_own_storage(ops_test, storage)

    logger.info("scaling to one unit")
    await scale_application(ops_test, app, 1)

    logger.info("check test database data")
    await validate_test_data(connection_string)

    logger.info("checking whether writes are increasing")
    await are_writes_increasing(ops_test)

    # Scale the database to three units.
    logger.info("scaling database to three units")
    await scale_application(ops_test, app, 3)

    # Verify all units are up and running.
    logger.info("waiting for the database service to start in all units")
    for unit in ops_test.model.applications[app].units:
        assert await is_postgresql_ready(ops_test, unit.name), (
            f"unit {unit.name} not restarted after cluster restart."
        )

    logger.info("check test database data")
    await validate_test_data(connection_string)

    logger.info("checking whether writes are increasing")
    await are_writes_increasing(ops_test)

    # Verify that all units are part of the same cluster.
    logger.info("checking whether all units are part of the same cluster")
    member_ips = await fetch_cluster_members(ops_test)
    ip_addresses = [
        await get_unit_address(ops_test, unit.name)
        for unit in ops_test.model.applications[app].units
    ]
    assert set(member_ips) == set(ip_addresses), "not all units are part of the same cluster."

    # Verify that no writes to the database were missed after stopping the writes.
    logger.info("checking whether no writes to the database were missed after stopping the writes")
    await check_writes(ops_test)
