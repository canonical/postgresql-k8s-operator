#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
import os

import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from .. import markers
from ..helpers import (
    DATABASE_APP_NAME,
    scale_application,
)
from .helpers import (
    apply_pvc_config,
    change_pv_reclaim_policy,
    change_pvc_pv_name,
    check_db,
    check_system_id_mismatch,
    create_db,
    delete_pvc,
    get_any_deatached_storage,
    get_pv,
    get_pvc,
    get_storage_id,
    is_postgresql_ready,
    is_storage_exists,
    remove_pv_claimref,
    remove_unit_force,
)

TEST_DATABASE_RELATION_NAME = "test_database"
DUP_DATABASE_APP_NAME = DATABASE_APP_NAME + "2"

logger = logging.getLogger(__name__)

env = os.environ
env["KUBECONFIG"] = os.path.expanduser("~/.kube/config")


@pytest.mark.group(1)
@markers.amd64_only  # TODO: remove after arm64 stable release
@pytest.mark.abort_on_fail
async def test_app_force_removal(ops_test: OpsTest):
    """Remove unit with force while storage is alive."""
    global primary_pv, primary_pvc
    # Deploy the charm.
    async with ops_test.fast_forward():
        await ops_test.model.deploy(
            DATABASE_APP_NAME,
            application_name=DATABASE_APP_NAME,
            num_units=1,
            channel="16/stable",
            trust=True,
            config={"profile": "testing"},
        )

        await ops_test.model.wait_for_idle(status="active", timeout=1000)

        assert ops_test.model.applications[DATABASE_APP_NAME].units[0].workload_status == "active"

        primary_name = ops_test.model.applications[DATABASE_APP_NAME].units[0].name

        logger.info("waiting for postgresql")
        for attempt in Retrying(stop=stop_after_delay(15 * 3), wait=wait_fixed(3), reraise=True):
            with attempt:
                assert await is_postgresql_ready(ops_test, primary_name)

        # Create test database to check there is no resources conflicts
        logger.info("creating db")
        await create_db(ops_test, DATABASE_APP_NAME, TEST_DATABASE_RELATION_NAME)

        assert primary_name

        logger.info(f"get pvc for {primary_name}")
        primary_pvc = get_pvc(ops_test, primary_name)

        assert primary_pvc

        logger.info(f"get pv for {primary_name}")
        primary_pv = get_pv(ops_test, primary_name)

        assert primary_pv

        logger.info("get storage id")
        storage_id = await get_storage_id(ops_test, primary_name)

        assert storage_id

        # Force remove unit without storage removal
        logger.info("scale to 0 with force")
        await remove_unit_force(ops_test, 1)

        # Storage will remain with deatached status
        logger.info("werifing is storage exists")
        for attempt in Retrying(stop=stop_after_delay(15 * 3), wait=wait_fixed(3), reraise=True):
            with attempt:
                assert await is_storage_exists(ops_test, storage_id)


@pytest.mark.group(1)
@markers.amd64_only  # TODO: remove after arm64 stable release
@pytest.mark.abort_on_fail
async def test_app_garbage_ignorance(ops_test: OpsTest):
    """Test charm deploy in dirty environment with garbage storage."""
    global primary_pv, primary_pvc
    async with ops_test.fast_forward():
        logger.info("checking garbage storage")
        garbage_storage = None
        for attempt in Retrying(stop=stop_after_delay(30 * 3), wait=wait_fixed(3), reraise=True):
            with attempt:
                garbage_storage = await get_any_deatached_storage(ops_test)

        logger.info("scale to 1")
        await scale_application(ops_test, DATABASE_APP_NAME, 1)

        # Timeout is increeced due to k8s Init:CrashLoopBackOff status of postgresql pod
        logger.info("waiting for idle")
        await ops_test.model.wait_for_idle(status="active", timeout=2000)
        assert ops_test.model.applications[DATABASE_APP_NAME].units[0].workload_status == "active"

        logger.info("getting primary")
        primary_name = ops_test.model.applications[DATABASE_APP_NAME].units[0].name

        assert primary_name

        logger.info("getting storage id")
        storage_id_str = await get_storage_id(ops_test, primary_name)

        assert storage_id_str == garbage_storage

        logger.info("waiting for postgresql")
        for attempt in Retrying(stop=stop_after_delay(15 * 3), wait=wait_fixed(3), reraise=True):
            with attempt:
                assert await is_postgresql_ready(ops_test, primary_name)

        # Check that test database is exists for duplicate application
        logger.info("checking db")
        assert await check_db(ops_test, DATABASE_APP_NAME, TEST_DATABASE_RELATION_NAME)

        logger.info("scale to 0")
        await scale_application(ops_test, DATABASE_APP_NAME, 0)

        logger.info("changing pv reclaim policy")
        primary_pv = change_pv_reclaim_policy(ops_test, primary_pv, "Retain")

        logger.info("remove application")
        await ops_test.model.remove_application(DATABASE_APP_NAME, block_until_done=True)

        logger.info(f"delete pvc {primary_pvc.metadata.name}")
        delete_pvc(ops_test, primary_pvc)


@pytest.mark.group(1)
@markers.amd64_only  # TODO: remove after arm64 stable release
@pytest.mark.abort_on_fail
async def test_app_resources_conflicts(ops_test: OpsTest):
    """Test application deploy in dirty environment with garbage storage from another application."""
    global primary_pv, primary_pvc
    async with ops_test.fast_forward():
        await ops_test.model.deploy(
            DATABASE_APP_NAME,
            application_name=DUP_DATABASE_APP_NAME,
            num_units=1,
            channel="16/stable",
            trust=True,
            config={"profile": "testing"},
        )

        logger.info("waiting for idle")
        await ops_test.model.wait_for_idle(status="active", timeout=1000)
        assert (
            ops_test.model.applications[DUP_DATABASE_APP_NAME].units[0].workload_status == "active"
        )

        dup_primary_name = ops_test.model.applications[DUP_DATABASE_APP_NAME].units[0].name

        assert dup_primary_name

        logger.info(f"get pvc for {dup_primary_name}")
        dup_primary_pvc = get_pvc(ops_test, dup_primary_name)

        assert dup_primary_pvc

        logger.info("scale to 0")
        await scale_application(ops_test, DUP_DATABASE_APP_NAME, 0)

        logger.info(f"load and change pv-name config for pvc {dup_primary_pvc.metadata.name}")
        dup_primary_pvc = change_pvc_pv_name(dup_primary_pvc, primary_pv.metadata.name)

        logger.info(f"delete pvc {dup_primary_pvc.metadata.name}")
        delete_pvc(ops_test, dup_primary_pvc)

        logger.info(f"remove claimref from pv {primary_pv.metadata.name}")
        remove_pv_claimref(ops_test, primary_pv)

        logger.info(f"apply pvc for {dup_primary_name}")
        apply_pvc_config(ops_test, dup_primary_pvc)

        logger.info("scale to 1")
        await ops_test.model.applications[DUP_DATABASE_APP_NAME].scale(1)

        logger.info("waiting for duplicate application to be blocked")
        try:
            await ops_test.model.wait_for_idle(
                apps=[DUP_DATABASE_APP_NAME], timeout=500, status="blocked"
            )
        except asyncio.TimeoutError:
            logger.info("Application is not in blocked state. Checking logs...")

        # Since application have postgresql db in storage from external application it should not be able to connect due to new password
        logger.info("checking operator password auth")
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3), reraise=True):
            with attempt:
                assert await check_system_id_mismatch(ops_test, dup_primary_name)
