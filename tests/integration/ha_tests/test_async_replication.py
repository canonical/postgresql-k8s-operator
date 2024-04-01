#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import contextlib
import logging
from asyncio import gather
from typing import Optional

import psycopg2
import pytest as pytest
from juju.model import Model
from lightkube import Client
from lightkube.resources.core_v1 import Pod
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from tests.integration.ha_tests.helpers import (
    are_writes_increasing,
    check_writes,
    get_standby_leader,
    get_sync_standby,
    start_continuous_writes,
)
from tests.integration.helpers import (
    APPLICATION_NAME,
    DATABASE_APP_NAME,
    build_and_deploy,
    get_leader_unit,
    get_password,
    get_primary,
    get_unit_address,
    wait_for_relation_removed_between,
)

logger = logging.getLogger(__name__)


FAST_INTERVAL = "60s"
IDLE_PERIOD = 30
TIMEOUT = 2000


@contextlib.asynccontextmanager
async def fast_forward(
    model: Model, fast_interval: str = "10s", slow_interval: Optional[str] = None
):
    """Adaptation of OpsTest.fast_forward to work with different models."""
    update_interval_key = "update-status-hook-interval"
    if slow_interval:
        interval_after = slow_interval
    else:
        interval_after = (await model.get_config())[update_interval_key]

    await model.set_config({update_interval_key: fast_interval})
    yield
    await model.set_config({update_interval_key: interval_after})


@pytest.fixture(scope="module")
def first_model(ops_test: OpsTest) -> Model:
    """Return the first model."""
    first_model = ops_test.model
    return first_model


@pytest.fixture(scope="module")
async def second_model(ops_test: OpsTest, first_model) -> Model:
    """Create and return the second model."""
    second_model_name = f"{first_model.info.name}-other"
    await ops_test._controller.add_model(second_model_name)
    second_model = Model()
    await second_model.connect(model_name=second_model_name)
    return second_model


@pytest.fixture
async def second_model_continuous_writes(second_model) -> None:
    """Cleans up continuous writes on the second model after a test run."""
    yield
    # Clear the written data at the end.
    for attempt in Retrying(stop=stop_after_delay(10), wait=wait_fixed(3), reraise=True):
        with attempt:
            action = (
                await second_model.applications[APPLICATION_NAME]
                .units[0]
                .run_action("clear-continuous-writes")
            )
            await action.wait()
            assert action.results["result"] == "True", "Unable to clear up continuous_writes table"


@pytest.mark.group(1)
@pytest.mark.juju3
@pytest.mark.abort_on_fail
async def test_deploy_async_replication_setup(
    ops_test: OpsTest, first_model: Model, second_model: Model
) -> None:
    """Build and deploy two PostgreSQL cluster in two separate models to test async replication."""
    await build_and_deploy(ops_test, 3, wait_for_idle=False)
    await build_and_deploy(ops_test, 3, wait_for_idle=False, model=second_model)
    await ops_test.model.deploy(APPLICATION_NAME, num_units=1)
    await second_model.deploy(APPLICATION_NAME, num_units=1)

    async with ops_test.fast_forward(), fast_forward(second_model):
        await gather(
            first_model.wait_for_idle(
                apps=[DATABASE_APP_NAME, APPLICATION_NAME],
                status="active",
                timeout=TIMEOUT,
            ),
            second_model.wait_for_idle(
                apps=[DATABASE_APP_NAME, APPLICATION_NAME],
                status="active",
                timeout=TIMEOUT,
            ),
        )


@pytest.mark.group(1)
@pytest.mark.juju3
@pytest.mark.abort_on_fail
async def test_async_replication(
    ops_test: OpsTest,
    first_model: Model,
    second_model: Model,
    continuous_writes,
) -> None:
    """Test async replication between two PostgreSQL clusters."""
    logger.info("starting continuous writes to the database")
    await start_continuous_writes(ops_test, DATABASE_APP_NAME)

    logger.info("checking whether writes are increasing")
    await are_writes_increasing(ops_test)

    await first_model.create_offer("async-primary", "async-primary", DATABASE_APP_NAME)
    await second_model.consume(
        f"admin/{first_model.info.name}.async-primary", controller=ops_test._controller
    )

    async with ops_test.fast_forward(FAST_INTERVAL), fast_forward(second_model, FAST_INTERVAL):
        await gather(
            first_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
            second_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
        )

    await second_model.relate(DATABASE_APP_NAME, "async-primary")

    async with ops_test.fast_forward(FAST_INTERVAL), fast_forward(second_model, FAST_INTERVAL):
        await gather(
            first_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
            second_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
        )

    logger.info("checking whether writes are increasing")
    await are_writes_increasing(ops_test)

    # Run the promote action.
    logger.info("Get leader unit")
    leader_unit = await get_leader_unit(ops_test, DATABASE_APP_NAME)
    assert leader_unit is not None, "No leader unit found"
    logger.info("promoting the first cluster")
    run_action = await leader_unit.run_action("promote-standby-cluster")
    await run_action.wait()

    async with ops_test.fast_forward(FAST_INTERVAL), fast_forward(second_model, FAST_INTERVAL):
        await gather(
            first_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
            second_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
        )

    logger.info("checking whether writes are increasing")
    await are_writes_increasing(ops_test)

    # Verify that no writes to the database were missed after stopping the writes
    # (check that all the units have all the writes).
    logger.info("checking whether no writes were lost")
    await check_writes(ops_test, extra_model=second_model)


@pytest.mark.group(1)
@pytest.mark.juju3
@pytest.mark.abort_on_fail
async def test_switchover(
    ops_test: OpsTest,
    first_model: Model,
    second_model: Model,
    second_model_continuous_writes,
):
    """Test switching over to the second cluster."""
    logger.info("breaking the relation")
    await second_model.applications[DATABASE_APP_NAME].remove_relation(
        "async-replica", "async-primary"
    )
    wait_for_relation_removed_between(ops_test, "async-primary", "async-replica", second_model)
    async with ops_test.fast_forward(FAST_INTERVAL), fast_forward(second_model, FAST_INTERVAL):
        await gather(
            first_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
            second_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
        )

    second_offer_command = f"offer {DATABASE_APP_NAME}:async-replica async-replica"
    await ops_test.juju(*second_offer_command.split())
    await second_model.consume(
        f"admin/{first_model.info.name}.async-replica", controller=ops_test._controller
    )

    async with ops_test.fast_forward(FAST_INTERVAL), fast_forward(second_model, FAST_INTERVAL):
        await gather(
            first_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
            second_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
        )

    await second_model.relate(DATABASE_APP_NAME, "async-replica")

    async with ops_test.fast_forward(FAST_INTERVAL), fast_forward(second_model, FAST_INTERVAL):
        await gather(
            first_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
            second_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
        )

    # Run the promote action.
    logger.info("Get leader unit")
    leader_unit = await get_leader_unit(ops_test, DATABASE_APP_NAME, model=second_model)
    assert leader_unit is not None, "No leader unit found"
    logger.info("promoting the second cluster")
    run_action = await leader_unit.run_action("promote-standby-cluster")
    await run_action.wait()

    async with ops_test.fast_forward(FAST_INTERVAL), fast_forward(second_model, FAST_INTERVAL):
        await gather(
            first_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
            second_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
        )

    logger.info("starting continuous writes to the database")
    await start_continuous_writes(ops_test, DATABASE_APP_NAME, model=second_model)

    logger.info("checking whether writes are increasing")
    await are_writes_increasing(ops_test, extra_model=second_model)


@pytest.mark.group(1)
@pytest.mark.juju3
@pytest.mark.abort_on_fail
async def test_promote_standby(
    ops_test: OpsTest,
    first_model: Model,
    second_model: Model,
    second_model_continuous_writes,
) -> None:
    """Test promoting the standby cluster."""
    logger.info("breaking the relation")
    await second_model.applications[DATABASE_APP_NAME].remove_relation(
        "async-primary", "async-replica"
    )
    wait_for_relation_removed_between(ops_test, "async-replica", "async-primary", first_model)
    async with ops_test.fast_forward(FAST_INTERVAL), fast_forward(second_model, FAST_INTERVAL):
        await gather(
            first_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
            second_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
        )
    # Run the promote action.
    logger.info("Get leader unit")
    leader_unit = await get_leader_unit(ops_test, DATABASE_APP_NAME)
    assert leader_unit is not None, "No leader unit found"
    logger.info("promoting the first cluster")
    run_action = await leader_unit.run_action("promote-standby-cluster")
    await run_action.wait()
    async with ops_test.fast_forward(FAST_INTERVAL), fast_forward(second_model, FAST_INTERVAL):
        await gather(
            first_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
            second_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
        )

    logger.info("removing the previous data")
    primary = await get_primary(ops_test)
    address = await get_unit_address(ops_test, primary)
    password = await get_password(ops_test)
    database_name = f'{APPLICATION_NAME.replace("-", "_")}_first_database'
    connection = None
    try:
        connection = psycopg2.connect(
            f"dbname={database_name} user=operator password={password} host={address}"
        )
        connection.autocommit = True
        cursor = connection.cursor()
        # cursor.execute(f"DROP DATABASE {database_name};")
        cursor.execute("DROP TABLE IF EXISTS continuous_writes;")
    except psycopg2.Error as e:
        assert False, f"Failed to drop continuous writes table: {e}"
    finally:
        if connection is not None:
            connection.close()

    logger.info("starting continuous writes to the database")
    await start_continuous_writes(ops_test, DATABASE_APP_NAME)

    logger.info("checking whether writes are increasing")
    await are_writes_increasing(ops_test)


@pytest.mark.group(1)
@pytest.mark.juju3
@pytest.mark.abort_on_fail
async def test_reestablish_relation(
    ops_test: OpsTest, first_model: Model, second_model: Model, continuous_writes
) -> None:
    """Test that the relation can be broken and re-established."""
    logger.info("starting continuous writes to the database")
    await start_continuous_writes(ops_test, DATABASE_APP_NAME)

    logger.info("checking whether writes are increasing")
    await are_writes_increasing(ops_test)

    logger.info("reestablishing the relation")
    await second_model.relate(DATABASE_APP_NAME, "async-primary")
    async with ops_test.fast_forward(FAST_INTERVAL), fast_forward(second_model, FAST_INTERVAL):
        await gather(
            first_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
            second_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
        )

    logger.info("checking whether writes are increasing")
    await are_writes_increasing(ops_test)

    # Run the promote action.
    logger.info("Get leader unit")
    leader_unit = await get_leader_unit(ops_test, DATABASE_APP_NAME)
    assert leader_unit is not None, "No leader unit found"
    logger.info("promoting the first cluster")
    run_action = await leader_unit.run_action("promote-standby-cluster")
    await run_action.wait()

    async with ops_test.fast_forward(FAST_INTERVAL), fast_forward(second_model, FAST_INTERVAL):
        await gather(
            first_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
            second_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
        )

    logger.info("checking whether writes are increasing")
    await are_writes_increasing(ops_test)

    # Verify that no writes to the database were missed after stopping the writes
    # (check that all the units have all the writes).
    logger.info("checking whether no writes were lost")
    await check_writes(ops_test, extra_model=second_model)


@pytest.mark.group(1)
@pytest.mark.juju3
@pytest.mark.abort_on_fail
async def test_async_replication_failover_in_main_cluster(
    ops_test: OpsTest, first_model: Model, second_model: Model, continuous_writes
) -> None:
    """Test that async replication fails over correctly."""
    logger.info("starting continuous writes to the database")
    await start_continuous_writes(ops_test, DATABASE_APP_NAME)

    logger.info("checking whether writes are increasing")
    await are_writes_increasing(ops_test)

    sync_standby = await get_sync_standby(first_model, DATABASE_APP_NAME)
    logger.info(f"Sync-standby: {sync_standby}")
    logger.info("deleting the sync-standby pod")
    client = Client(namespace=first_model.info.name)
    client.delete(Pod, name=sync_standby.replace("/", "-"))

    async with ops_test.fast_forward(FAST_INTERVAL), fast_forward(second_model, FAST_INTERVAL):
        await gather(
            first_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
            second_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
        )

    # Check that the sync-standby unit is not the same as before.
    new_sync_standby = await get_sync_standby(first_model, DATABASE_APP_NAME)
    logger.info(f"New sync-standby: {new_sync_standby}")
    assert new_sync_standby != sync_standby, "Sync-standby is the same as before"

    logger.info("Ensure continuous_writes after the crashed unit")
    await are_writes_increasing(ops_test)

    # Verify that no writes to the database were missed after stopping the writes
    # (check that all the units have all the writes).
    logger.info("checking whether no writes were lost")
    await check_writes(ops_test, extra_model=second_model)


@pytest.mark.group(1)
@pytest.mark.juju3
@pytest.mark.abort_on_fail
async def test_async_replication_failover_in_secondary_cluster(
    ops_test: OpsTest, first_model: Model, second_model: Model, continuous_writes
) -> None:
    """Test that async replication fails back correctly."""
    logger.info("starting continuous writes to the database")
    await start_continuous_writes(ops_test, DATABASE_APP_NAME)

    logger.info("checking whether writes are increasing")
    await are_writes_increasing(ops_test)

    standby_leader = await get_standby_leader(second_model, DATABASE_APP_NAME)
    logger.info(f"Standby leader: {standby_leader}")
    logger.info("deleting the standby leader pod")
    client = Client(namespace=second_model.info.name)
    client.delete(Pod, name=standby_leader.replace("/", "-"))

    async with ops_test.fast_forward(FAST_INTERVAL), fast_forward(second_model, FAST_INTERVAL):
        await gather(
            first_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
            second_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
        )

    logger.info("Ensure continuous_writes after the crashed unit")
    await are_writes_increasing(ops_test)

    # Verify that no writes to the database were missed after stopping the writes
    # (check that all the units have all the writes).
    logger.info("checking whether no writes were lost")
    await check_writes(ops_test, extra_model=second_model)
