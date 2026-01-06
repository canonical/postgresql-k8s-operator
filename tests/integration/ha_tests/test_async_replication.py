#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import contextlib
import logging
import subprocess
from asyncio import gather

import psycopg2
import pytest as pytest
from juju.model import Model
from lightkube import Client
from lightkube.resources.core_v1 import Pod
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from .. import architecture, markers
from ..helpers import (
    APPLICATION_NAME,
    CHARM_BASE,
    DATABASE_APP_NAME,
    build_and_deploy,
    get_leader_unit,
    get_password,
    get_primary,
    get_unit_address,
    scale_application,
    wait_for_relation_removed_between,
)
from .helpers import (
    are_writes_increasing,
    check_writes,
    get_leader,
    get_standby_leader,
    start_continuous_writes,
)

logger = logging.getLogger(__name__)


CLUSTER_SIZE = 3
FAST_INTERVAL = "10s"
IDLE_PERIOD = 5
TIMEOUT = 2000


@contextlib.asynccontextmanager
async def fast_forward(model: Model, fast_interval: str = "10s", slow_interval: str | None = None):
    """Adaptation of OpsTest.fast_forward to work with different models."""
    update_interval_key = "update-status-hook-interval"
    interval_after = (
        slow_interval if slow_interval else (await model.get_config())[update_interval_key]
    )

    await model.set_config({update_interval_key: fast_interval})
    yield
    await model.set_config({update_interval_key: interval_after})


@pytest.fixture(scope="module")
def first_model(ops_test: OpsTest) -> Model:
    """Return the first model."""
    first_model = ops_test.model
    return first_model


@pytest.fixture(scope="module")
async def second_model(ops_test: OpsTest, first_model, request) -> Model:
    """Create and return the second model."""
    second_model_name = f"{first_model.info.name}-other"
    if second_model_name not in await ops_test._controller.list_models():
        await ops_test._controller.add_model(second_model_name)
        subprocess.run(["juju", "switch", second_model_name], check=True)
        subprocess.run(
            ["juju", "set-model-constraints", f"arch={architecture.architecture}"], check=True
        )
        subprocess.run(["juju", "switch", first_model.info.name], check=True)
    second_model = Model()
    await second_model.connect(model_name=second_model_name)
    yield second_model
    if request.config.getoption("--keep-models"):
        return
    logger.info("Destroying second model")
    await ops_test._controller.destroy_model(second_model_name, destroy_storage=True)


@pytest.fixture
async def second_model_continuous_writes(second_model) -> None:
    """Cleans up continuous writes on the second model after a test run."""
    yield
    # Clear the written data at the end.
    for attempt in Retrying(stop=stop_after_delay(10), wait=wait_fixed(3), reraise=True):
        with attempt:
            action = (
                await second_model
                .applications[APPLICATION_NAME]
                .units[0]
                .run_action("clear-continuous-writes")
            )
            await action.wait()
            assert action.results["result"] == "True", "Unable to clear up continuous_writes table"


@markers.juju3
@pytest.mark.abort_on_fail
async def test_deploy_async_replication_setup(
    ops_test: OpsTest, charm, first_model: Model, second_model: Model
) -> None:
    """Build and deploy two PostgreSQL cluster in two separate models to test async replication."""
    await build_and_deploy(ops_test, charm, CLUSTER_SIZE, wait_for_idle=False)
    await build_and_deploy(ops_test, charm, CLUSTER_SIZE, wait_for_idle=False, model=second_model)
    await ops_test.model.deploy(
        APPLICATION_NAME, channel="latest/edge", num_units=1, base=CHARM_BASE
    )
    await second_model.deploy(
        APPLICATION_NAME, channel="latest/edge", num_units=1, base=CHARM_BASE
    )

    async with ops_test.fast_forward(), fast_forward(second_model):
        await gather(
            first_model.wait_for_idle(apps=[APPLICATION_NAME], status="blocked"),
            second_model.wait_for_idle(apps=[APPLICATION_NAME], status="blocked"),
        )
        await gather(
            first_model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=TIMEOUT),
            second_model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=TIMEOUT),
        )


@markers.juju3
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

    first_offer_command = f"offer {DATABASE_APP_NAME}:replication-offer replication-offer"
    await ops_test.juju(*first_offer_command.split())
    first_consume_command = (
        f"consume -m {second_model.info.name} admin/{first_model.info.name}.replication-offer"
    )
    await ops_test.juju(*first_consume_command.split())

    async with ops_test.fast_forward(FAST_INTERVAL), fast_forward(second_model, FAST_INTERVAL):
        await gather(
            first_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
            second_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
        )

    await second_model.relate(DATABASE_APP_NAME, "replication-offer")

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
    run_action = await leader_unit.run_action("create-replication")
    await run_action.wait()
    assert (run_action.results.get("return-code", None) == 0) or (
        run_action.results.get("Code", None) == "0"
    ), "Promote action failed"

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


@markers.juju3
@pytest.mark.abort_on_fail
async def test_switchover(
    ops_test: OpsTest,
    first_model: Model,
    second_model: Model,
    second_model_continuous_writes,
):
    """Test switching over to the second cluster."""
    second_offer_command = f"offer {DATABASE_APP_NAME}:replication replication"
    await ops_test.juju(*second_offer_command.split())
    second_consume_command = (
        f"consume -m {second_model.info.name} admin/{first_model.info.name}.replication"
    )
    await ops_test.juju(*second_consume_command.split())

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
    run_action = await leader_unit.run_action("promote-to-primary", force=True, scope="cluster")
    await run_action.wait()
    assert (run_action.results.get("return-code", None) == 0) or (
        run_action.results.get("Code", None) == "0"
    ), "Promote action failed"

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


@markers.juju3
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
        "replication", "replication-offer"
    )
    wait_for_relation_removed_between(ops_test, "replication-offer", "replication", second_model)
    async with ops_test.fast_forward(FAST_INTERVAL), fast_forward(second_model, FAST_INTERVAL):
        await gather(
            first_model.wait_for_idle(
                apps=[DATABASE_APP_NAME], idle_period=IDLE_PERIOD, timeout=TIMEOUT
            ),
            first_model.block_until(
                lambda: first_model.applications[DATABASE_APP_NAME].status == "blocked",
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
    run_action = await leader_unit.run_action("promote-to-primary", scope="cluster")
    await run_action.wait()
    assert (run_action.results.get("return-code", None) == 0) or (
        run_action.results.get("Code", None) == "0"
    ), "Promote action failed"

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
    database_name = f"{APPLICATION_NAME.replace('-', '_')}_database"
    connection = None
    try:
        connection = psycopg2.connect(
            f"dbname={database_name} user=operator password={password} host={address}"
        )
        connection.autocommit = True
        cursor = connection.cursor()
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


@markers.juju3
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
    await second_model.relate(DATABASE_APP_NAME, "replication-offer")
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
    run_action = await leader_unit.run_action("create-replication")
    await run_action.wait()
    assert (run_action.results.get("return-code", None) == 0) or (
        run_action.results.get("Code", None) == "0"
    ), "Promote action failed"

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


@markers.juju3
@pytest.mark.abort_on_fail
async def test_async_replication_failover_in_main_cluster(
    ops_test: OpsTest, first_model: Model, second_model: Model, continuous_writes
) -> None:
    """Test that async replication fails over correctly."""
    logger.info("starting continuous writes to the database")
    await start_continuous_writes(ops_test, DATABASE_APP_NAME)

    logger.info("checking whether writes are increasing")
    await are_writes_increasing(ops_test)

    primary = await get_leader(first_model, DATABASE_APP_NAME)
    logger.info(f"Primary: {primary}")
    logger.info("deleting the primary pod")
    client = Client(namespace=first_model.info.name)
    client.delete(Pod, name=primary.replace("/", "-"))

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
    new_primary = await get_leader(first_model, DATABASE_APP_NAME)
    logger.info(f"New sync-standby: {new_primary}")
    assert new_primary != primary, "Sync-standby is the same as before"

    logger.info("Ensure continuous_writes after the crashed unit")
    await are_writes_increasing(ops_test)

    # Verify that no writes to the database were missed after stopping the writes
    # (check that all the units have all the writes).
    logger.info("checking whether no writes were lost")
    await check_writes(ops_test, extra_model=second_model)


@markers.juju3
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


@markers.juju3
@pytest.mark.abort_on_fail
async def test_scaling(
    ops_test: OpsTest, first_model: Model, second_model: Model, continuous_writes
) -> None:
    """Test that async replication works when scaling the clusters."""
    logger.info("starting continuous writes to the database")
    await start_continuous_writes(ops_test, DATABASE_APP_NAME)

    logger.info("checking whether writes are increasing")
    await are_writes_increasing(ops_test)

    async with ops_test.fast_forward(FAST_INTERVAL), fast_forward(second_model, FAST_INTERVAL):
        logger.info("scaling out the first cluster")
        first_cluster_original_size = len(first_model.applications[DATABASE_APP_NAME].units)
        await scale_application(ops_test, DATABASE_APP_NAME, first_cluster_original_size + 1)

        logger.info("checking whether writes are increasing")
        await are_writes_increasing(ops_test, extra_model=second_model)

        logger.info("scaling out the second cluster")
        second_cluster_original_size = len(second_model.applications[DATABASE_APP_NAME].units)
        await scale_application(
            ops_test, DATABASE_APP_NAME, second_cluster_original_size + 1, model=second_model
        )

        logger.info("checking whether writes are increasing")
        await are_writes_increasing(ops_test, extra_model=second_model)

        logger.info("scaling in the first cluster")
        await scale_application(ops_test, DATABASE_APP_NAME, first_cluster_original_size)

        logger.info("checking whether writes are increasing")
        await are_writes_increasing(ops_test, extra_model=second_model)

        logger.info("scaling in the second cluster")
        await scale_application(
            ops_test, DATABASE_APP_NAME, second_cluster_original_size, model=second_model
        )

        logger.info("checking whether writes are increasing")
        await are_writes_increasing(ops_test, extra_model=second_model)

    # Verify that no writes to the database were missed after stopping the writes
    # (check that all the units have all the writes).
    logger.info("checking whether no writes were lost")
    await check_writes(ops_test, extra_model=second_model)
