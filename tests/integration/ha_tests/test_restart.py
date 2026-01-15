#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import pytest as pytest
from kubernetes import config, dynamic
from kubernetes.client import api_client
from pytest_operator.plugin import OpsTest

from ..helpers import (
    APPLICATION_NAME,
    DATABASE_APP_NAME,
    app_name,
    build_and_deploy,
    get_application_units,
    get_cluster_members,
    get_primary,
    get_unit_address,
)
from .helpers import (
    are_writes_increasing,
    check_writes,
    remove_charm_code,
    start_continuous_writes,
)

logger = logging.getLogger(__name__)


CLUSTER_SIZE = 3


@pytest.mark.abort_on_fail
async def test_deploy(ops_test: OpsTest, charm) -> None:
    """Build and deploy a PostgreSQL cluster and a test application."""
    await build_and_deploy(ops_test, charm, CLUSTER_SIZE, wait_for_idle=False)
    if not await app_name(ops_test, APPLICATION_NAME):
        await ops_test.model.deploy(APPLICATION_NAME, num_units=1)

    await ops_test.model.relate(DATABASE_APP_NAME, f"{APPLICATION_NAME}:database")

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME, APPLICATION_NAME],
            status="active",
            timeout=1000,
            raise_on_error=False,
        )


@pytest.mark.abort_on_fail
async def test_restart(ops_test: OpsTest, continuous_writes) -> None:
    """Test restart of all the units simultaneously."""
    logger.info("starting continuous writes to the database")
    await start_continuous_writes(ops_test, DATABASE_APP_NAME)

    logger.info("checking whether writes are increasing")
    await are_writes_increasing(ops_test)

    logger.info(
        "removing charm code from one non-primary unit to simulate a crash and prevent firing the update-charm hook"
    )
    primary = await get_primary(ops_test)
    status = await ops_test.model.get_status()
    for unit in status.applications[DATABASE_APP_NAME].units:
        if unit != primary:
            non_primary = unit
            break
    await remove_charm_code(ops_test, non_primary)
    logger.info(f"removed charm code from {non_primary}")

    logger.info("restarting all the units by deleting their pods")
    client = dynamic.DynamicClient(api_client.ApiClient(configuration=config.load_kube_config()))
    api = client.resources.get(api_version="v1", kind="Pod")
    api.delete(
        namespace=ops_test.model.info.name,
        label_selector=f"app.kubernetes.io/name={DATABASE_APP_NAME}",
    )
    await ops_test.model.block_until(
        lambda: all(
            unit.workload_status == "error"
            for unit in ops_test.model.units.values()
            if unit.name == non_primary
        )
    )

    # Resolve the error on the non-primary unit.
    for unit in ops_test.model.units.values():
        if unit.name == non_primary and unit.workload_status == "error":
            logger.info(f"resolving {non_primary} error")
            await unit.resolved(retry=False)
            break

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME], status="active", raise_on_error=False, timeout=300
        )

    # Check that all replication slots are present in the primary
    # (by checking the list of cluster members).
    logger.info(
        "checking that all the replication slots are present in the primary by checking the list of cluster members"
    )
    primary = await get_primary(ops_test)
    address = await get_unit_address(ops_test, primary)
    assert get_cluster_members(address) == get_application_units(ops_test, DATABASE_APP_NAME)

    logger.info("ensure continuous_writes after the crashed unit")
    await are_writes_increasing(ops_test)

    # Verify that no writes to the database were missed after stopping the writes
    # (check that all the units have all the writes).
    logger.info("checking whether no writes were lost")
    await check_writes(ops_test)
