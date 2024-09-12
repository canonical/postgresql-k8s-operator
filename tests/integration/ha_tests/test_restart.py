#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
from asyncio import gather

import pytest as pytest
from kubernetes import config, dynamic
from kubernetes.client import api_client
from pytest_operator.plugin import OpsTest

from ..helpers import (
    APPLICATION_NAME,
    DATABASE_APP_NAME,
    build_and_deploy,
    get_application_units,
    get_cluster_members,
    get_primary,
    get_unit_address,
)
from .helpers import (
    are_writes_increasing,
    check_writes,
    inject_stop_hook_fault,
    start_continuous_writes,
)

logger = logging.getLogger(__name__)


CLUSTER_SIZE = 3


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_deploy(ops_test: OpsTest) -> None:
    """Build and deploy a PostgreSQL cluster and a test application."""
    await build_and_deploy(ops_test, CLUSTER_SIZE, wait_for_idle=False)
    await ops_test.model.deploy(APPLICATION_NAME, num_units=1)

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME, APPLICATION_NAME],
            status="active",
            timeout=1000,
            raise_on_error=False,
        )


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_restart(ops_test: OpsTest, continuous_writes) -> None:
    """Test restart of all the units simultaneously."""
    logger.info("starting continuous writes to the database")
    await start_continuous_writes(ops_test, DATABASE_APP_NAME)

    logger.info("checking whether writes are increasing")
    await are_writes_increasing(ops_test)

    logger.info(
        "patch the stop hook of one non-primary unit to simulate a crash and prevent firing the update-charm hook"
    )
    primary = await get_primary(ops_test)
    status = await ops_test.model.get_status()
    for unit in status.applications[DATABASE_APP_NAME].units:
        if unit != primary:
            non_primary = unit
            break
    await inject_stop_hook_fault(ops_test, non_primary)

    # Disable the automatic retry of hooks to avoid the stop hook being retried.
    await ops_test.model.set_config({"automatically-retry-hooks": "false"})

    logger.info("restarting all the units by deleting their pods")
    client = dynamic.DynamicClient(api_client.ApiClient(configuration=config.load_kube_config()))
    api = client.resources.get(api_version="v1", kind="Pod")
    api.delete(
        namespace=ops_test.model.info.name,
        label_selector=f"app.kubernetes.io/name={DATABASE_APP_NAME}",
    )

    async with ops_test.fast_forward():
        await gather(
            ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1000)
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
