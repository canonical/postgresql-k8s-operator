#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import time

import pytest
from pytest_operator.plugin import OpsTest
from tenacity import RetryError, Retrying, stop_after_delay, wait_fixed

from tests.integration.ha_tests.helpers import (
    METADATA,
    app_name,
    change_master_start_timeout,
    count_writes,
    get_primary,
    kill_process,
    postgresql_ready,
    secondary_up_to_date,
    start_continuous_writes,
    stop_continuous_writes,
)

PATRONI_PROCESS = "/usr/local/bin/patroni"
POSTGRESQL_PROCESS = "postgres"


@pytest.mark.abort_on_fail
@pytest.mark.ha_tests
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


@pytest.mark.ha_tests
@pytest.mark.parametrize("process", [POSTGRESQL_PROCESS, PATRONI_PROCESS])
async def test_kill_db_process(ops_test: OpsTest, process: str, continuous_writes) -> None:
    # locate primary unit
    app = await app_name(ops_test)
    primary_name = await get_primary(ops_test, app)

    await start_continuous_writes(ops_test, app)

    await change_master_start_timeout(ops_test, 0)
    await kill_process(ops_test, primary_name, POSTGRESQL_PROCESS, kill_code="SIGKILL")
    await change_master_start_timeout(ops_test, 300)

    # verify new writes are continuing by counting the number of writes before and after a 5 second
    # wait
    writes = await count_writes(ops_test)
    time.sleep(5)
    more_writes = await count_writes(ops_test)
    assert more_writes > writes, "writes not continuing to DB"

    # sleep for twice the median election time
    time.sleep(30 * 2)

    # verify that db service got restarted and is ready
    assert await postgresql_ready(ops_test, primary_name)

    # verify that a new primary gets elected (ie old primary is secondary)
    new_primary_name = await get_primary(ops_test, app)
    assert new_primary_name != primary_name

    # verify that no writes to the db were missed
    total_expected_writes = await stop_continuous_writes(ops_test)
    try:
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
            with attempt:
                actual_writes = await count_writes(ops_test)
                assert total_expected_writes == actual_writes, "writes to the db were missed."
    except RetryError:
        raise

    # verify that old primary is up to date.
    assert await secondary_up_to_date(
        ops_test, primary_name, total_expected_writes
    ), "secondary not up to date with the cluster after restarting."
