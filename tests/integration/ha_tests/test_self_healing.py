#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from tests.integration.ha_tests.helpers import (
    METADATA,
    app_name,
    change_master_start_timeout,
    change_wal_settings,
    count_writes,
    fetch_cluster_members,
    get_master_start_timeout,
    get_primary,
    is_replica,
    list_wal_files,
    postgresql_ready,
    secondary_up_to_date,
    send_signal_to_process,
    stop_continuous_writes,
)
from tests.integration.helpers import (
    db_connect,
    get_password,
    get_unit_address,
    run_command_on_unit,
)

PATRONI_PROCESS = "/usr/local/bin/patroni"
POSTGRESQL_PROCESS = "postgres"
DB_PROCESSES = [POSTGRESQL_PROCESS, PATRONI_PROCESS]


@pytest.mark.abort_on_fail
@pytest.mark.ha_self_healing_tests
async def test_build_and_deploy(ops_test: OpsTest) -> None:
    """Build and deploy three unit of PostgreSQL."""
    # It is possible for users to provide their own cluster for HA testing. Hence, check if there
    # is a pre-existing cluster.
    if await app_name(ops_test):
        return

    charm = await ops_test.build_charm(".")
    async with ops_test.fast_forward():
        await ops_test.model.deploy(
            charm,
            resources={
                "postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"]
            },
            num_units=3,
            trust=True,
        )
        await ops_test.model.wait_for_idle(status="active", timeout=1000)


@pytest.mark.ha_self_healing_tests
@pytest.mark.parametrize("process", [POSTGRESQL_PROCESS])
async def test_freeze_db_process(
    ops_test: OpsTest, process: str, continuous_writes, master_start_timeout
) -> None:
    # Locate primary unit.
    app = await app_name(ops_test)
    primary_name = await get_primary(ops_test, app)

    # Freeze the database process.
    await send_signal_to_process(ops_test, primary_name, process, "SIGSTOP")

    async with ops_test.fast_forward():
        # Verify new writes are continuing by counting the number of writes before and after a
        # 2 minutes wait (a db process freeze takes more time to trigger a fail-over).
        writes = await count_writes(ops_test, primary_name)
        for attempt in Retrying(stop=stop_after_delay(60 * 2), wait=wait_fixed(3)):
            with attempt:
                more_writes = await count_writes(ops_test, primary_name)
                assert more_writes > writes, "writes not continuing to DB"

        # Verify that a new primary gets elected (ie old primary is secondary).
        new_primary_name = await get_primary(ops_test, app, down_unit=primary_name)
        assert new_primary_name != primary_name

        # Un-freeze the old primary.
        await send_signal_to_process(ops_test, primary_name, process, "SIGCONT")

        # Verify that the database service got restarted and is ready in the old primary.
        assert await postgresql_ready(ops_test, primary_name)

    # Verify that the old primary is now a replica.
    assert await is_replica(
        ops_test, primary_name
    ), "there are more than one primary in the cluster."

    # Verify that all units are part of the same cluster.
    member_ips = await fetch_cluster_members(ops_test)
    ip_addresses = [
        await get_unit_address(ops_test, unit.name)
        for unit in ops_test.model.applications[app].units
    ]
    assert set(member_ips) == set(ip_addresses), "not all units are part of the same cluster."

    # Verify that no writes to the database were missed after stopping the writes.
    total_expected_writes = await stop_continuous_writes(ops_test)
    for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
        with attempt:
            actual_writes = await count_writes(ops_test)
            assert total_expected_writes == actual_writes, "writes to the db were missed."

    # Verify that old primary is up-to-date.
    assert await secondary_up_to_date(
        ops_test, primary_name, total_expected_writes
    ), "secondary not up to date with the cluster after restarting."


@pytest.mark.ha_self_healing_tests
@pytest.mark.parametrize("process", DB_PROCESSES)
async def test_restart_db_process(
    ops_test: OpsTest, process: str, continuous_writes, master_start_timeout
) -> None:
    # Locate primary unit.
    app = await app_name(ops_test)
    primary_name = await get_primary(ops_test, app)

    # Restart the database process.
    await send_signal_to_process(ops_test, primary_name, process, "SIGTERM")

    async with ops_test.fast_forward():
        # Verify new writes are continuing by counting the number of writes before and after a
        # 2 minutes wait (a db process freeze takes more time to trigger a fail-over).
        writes = await count_writes(ops_test, primary_name)
        for attempt in Retrying(stop=stop_after_delay(60 * 2), wait=wait_fixed(3)):
            with attempt:
                more_writes = await count_writes(ops_test, primary_name)
                assert more_writes > writes, "writes not continuing to DB"

        # Verify that the database service got restarted and is ready in the old primary.
        assert await postgresql_ready(ops_test, primary_name)

    # Verify that a new primary gets elected (ie old primary is secondary).
    new_primary_name = await get_primary(ops_test, app, down_unit=primary_name)
    assert new_primary_name != primary_name

    # Verify that the old primary is now a replica.
    assert await is_replica(
        ops_test, primary_name
    ), "there are more than one primary in the cluster."

    # Verify that all units are part of the same cluster.
    member_ips = await fetch_cluster_members(ops_test)
    ip_addresses = [
        await get_unit_address(ops_test, unit.name)
        for unit in ops_test.model.applications[app].units
    ]
    assert set(member_ips) == set(ip_addresses), "not all units are part of the same cluster."

    # Verify that no writes to the database were missed after stopping the writes.
    total_expected_writes = await stop_continuous_writes(ops_test)
    for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
        with attempt:
            actual_writes = await count_writes(ops_test)
            assert total_expected_writes == actual_writes, "writes to the db were missed."

    # Verify that old primary is up-to-date.
    assert await secondary_up_to_date(
        ops_test, primary_name, total_expected_writes
    ), "secondary not up to date with the cluster after restarting."


@pytest.mark.ha_self_healing_tests
async def test_forceful_restart_without_data_and_transaction_logs(
    ops_test: OpsTest,
    continuous_writes,
    master_start_timeout,
    wal_settings,
) -> None:
    """A forceful restart with deleted data and without transaction logs (forced clone)."""
    app = await app_name(ops_test)
    primary_name = await get_primary(ops_test, app)

    # Copy data dir content removal script.
    await ops_test.juju(
        "scp", "tests/integration/ha_tests/clean-data-dir.sh", f"{primary_name}:/tmp"
    )

    # Change the "master_start_timeout" parameter to speed up the fail-over.
    original_master_start_timeout = await get_master_start_timeout(ops_test)
    await change_master_start_timeout(ops_test, 0)

    # Stop the systemd service on the primary unit.
    await run_command_on_unit(ops_test, primary_name, "/charm/bin/pebble stop postgresql")

    # Data removal runs within a script, so it allows `*` expansion.
    return_code, _, _ = await ops_test.juju(
        "ssh",
        primary_name,
        "chmod",
        "+x",
        "/tmp/clean-data-dir.sh",
        "&&",
        "/tmp/clean-data-dir.sh",
    )
    assert return_code == 0, "Failed to remove data directory"

    async with ops_test.fast_forward():
        # Verify that a new primary gets elected (ie old primary is secondary).
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
            with attempt:
                new_primary_name = await get_primary(ops_test, app)
                assert new_primary_name is not None
                assert new_primary_name != primary_name

        # Revert the "master_start_timeout" parameter to avoid fail-over again.
        await change_master_start_timeout(ops_test, original_master_start_timeout)

        # Verify new writes are continuing by counting the number of writes before and after a
        # 60 seconds wait (this is a little more than the loop wait configuration, that is
        # considered to trigger a fail-over after master_start_timeout is changed).
        writes = await count_writes(ops_test, primary_name)
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
            with attempt:
                more_writes = await count_writes(ops_test, primary_name)
                assert more_writes > writes, "writes not continuing to DB"

        # Change some settings to enable WAL rotation.
        for unit in ops_test.model.applications[app].units:
            if unit.name == primary_name:
                continue
            await change_wal_settings(ops_test, unit.name, 32, 32, 1)

        # Rotate the WAL segments.
        files = await list_wal_files(ops_test, app)
        host = await get_unit_address(ops_test, new_primary_name)
        password = await get_password(ops_test, down_unit=new_primary_name)
        with db_connect(host, password) as connection:
            connection.autocommit = True
            with connection.cursor() as cursor:
                # Run some commands to make PostgreSQL do WAL rotation.
                cursor.execute("SELECT pg_switch_wal();")
                cursor.execute("CHECKPOINT;")
                cursor.execute("SELECT pg_switch_wal();")
        connection.close()
        new_files = await list_wal_files(ops_test, app)
        # Check that the WAL was correctly rotated.
        for unit_name in files:
            assert not files[unit_name].intersection(
                new_files
            ), "WAL segments weren't correctly rotated"

        # Start the systemd service in the old primary.
        await run_command_on_unit(ops_test, primary_name, "/charm/bin/pebble start postgresql")

        # Verify that the database service got restarted and is ready in the old primary.
        assert await postgresql_ready(ops_test, primary_name)

    # Verify that the old primary is now a replica.
    assert is_replica(ops_test, primary_name), "there are more than one primary in the cluster."

    # Verify that all units are part of the same cluster.
    member_ips = await fetch_cluster_members(ops_test)
    ip_addresses = [
        await get_unit_address(ops_test, unit.name)
        for unit in ops_test.model.applications[app].units
    ]
    assert set(member_ips) == set(ip_addresses), "not all units are part of the same cluster."

    # Verify that no writes to the database were missed after stopping the writes.
    total_expected_writes = await stop_continuous_writes(ops_test)
    for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
        with attempt:
            actual_writes = await count_writes(ops_test)
            assert total_expected_writes == actual_writes, "writes to the db were missed."

    # Verify that old primary is up-to-date.
    assert await secondary_up_to_date(
        ops_test, primary_name, total_expected_writes
    ), "secondary not up to date with the cluster after restarting."
