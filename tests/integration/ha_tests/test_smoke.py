#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest
import os
from psycopg2 import sql
from juju import tag
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_attempt, stop_after_delay, wait_fixed
from time import sleep
from ..juju_ import juju_major_version
from asyncio import TimeoutError

from ..helpers import (
    CHARM_SERIES,
    scale_application,
    APPLICATION_NAME,
    DATABASE_APP_NAME,
    get_primary,
    get_existing_k8s_resources,
)

from .helpers import (
    is_postgresql_ready,
    get_any_deatached_storage,
    check_password_auth,
    create_db,
    check_db,
    get_storage_id,
    is_storage_exists,
    is_pods_exists,
    remove_unit_force,
)

TEST_DATABASE_RELATION_NAME = "test_database"
DUP_DATABASE_APP_NAME = DATABASE_APP_NAME + "2"

logger = logging.getLogger(__name__)

env = os.environ
env["KUBECONFIG"] = os.path.expanduser("~/.kube/config")
print(f"Model Name: {DATABASE_APP_NAME}")

@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_app_removal(ops_test: OpsTest):
    return
    """Test all recoureces is removed after application removal"""

    # Deploy the charm.
    async with ops_test.fast_forward():
        await ops_test.model.deploy(
            DATABASE_APP_NAME,
            application_name=DATABASE_APP_NAME,
            num_units=1,
            channel="14/stable",
            series=CHARM_SERIES,
        )

        # Reducing the update status frequency to speed up the triggering of deferred events.
        await ops_test.model.set_config({"update-status-hook-interval": "10s"})

        await ops_test.model.wait_for_idle(status="active", timeout=1000)

        assert ops_test.model.applications[DATABASE_APP_NAME].units[0].workload_status == "active"

        primary_name = None
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
            with attempt:
                primary_name = await get_primary(ops_test, DATABASE_APP_NAME)

        assert primary_name is not None

        assert await is_postgresql_ready(ops_test, primary_name)

        # Check if pod exists
        assert is_pods_exists(ops_test, primary_name)

        # Check if k8s resources exists
        assert len(get_existing_k8s_resources(ops_test.model.info.name,DATABASE_APP_NAME)) != 0

        storage_id = await get_storage_id(ops_test, primary_name)

        assert await is_storage_exists(ops_test, storage_id)

        await ops_test.model.remove_application(DATABASE_APP_NAME, block_until_done=True)

        # Check if storage removed after application removal
        assert not await is_storage_exists(ops_test, storage_id)

        # Check if pods are removed after application removal
        assert not is_pods_exists(ops_test, primary_name)

        # Check if k8s resources are removed after application removal
        assert len(get_existing_k8s_resources(ops_test.model.info.name, DATABASE_APP_NAME)) == 0



@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_app_force_removal(ops_test: OpsTest):
    """Remove unit with force while storage is alive"""

    # Deploy the charm.
    async with ops_test.fast_forward():
        logger.info("deploying charm")
        await ops_test.model.deploy(
            DATABASE_APP_NAME,
            application_name=DATABASE_APP_NAME,
            num_units=1,
            channel="14/stable",
            series=CHARM_SERIES,
            storage={"pgdata": {"pool": "kubernetes", "size": 8046}},
        )

        # Reducing the update status frequency to speed up the triggering of deferred events.
        await ops_test.model.set_config({"update-status-hook-interval": "10s"})

        logger.info("waiting for idle")
        await ops_test.model.wait_for_idle(status="active", timeout=1000)
        assert ops_test.model.applications[DATABASE_APP_NAME].units[0].workload_status == "active"

        logger.info("getting primary")
        primary_name = None
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3), reraise=True):
            with attempt:
                primary_name = await get_primary(ops_test, DATABASE_APP_NAME)
    
        logger.info("waiting for postgresql")
        for attempt in Retrying(stop=stop_after_delay(15 * 3), wait=wait_fixed(3), reraise=True):
            with attempt:
                assert await is_postgresql_ready(ops_test, primary_name)

        logger.info("getting storage id")
        storage_id = await get_storage_id(ops_test, primary_name)

        logger.info("werifing is storage exists")
        for attempt in Retrying(stop=stop_after_delay(15 * 3), wait=wait_fixed(3), reraise=True):
            with attempt:
                assert await is_storage_exists(ops_test, storage_id)

        logger.info("werifing is pods exists")
        assert is_pods_exists(ops_test, primary_name)

        # Create test database to check there is no resouces conflicts
        logger.info("creating db")
        await create_db(ops_test, DATABASE_APP_NAME, TEST_DATABASE_RELATION_NAME)

        # Remove application witout storage removal
        logger.info("scale to 0")
        await scale_application(ops_test, DATABASE_APP_NAME, 0)

        logger.info("werifing is pods do not exists")
        assert not is_pods_exists(ops_test, primary_name)

        # Storage will remain with deatached status
        logger.info("werifing is storage exists")
        for attempt in Retrying(stop=stop_after_delay(15 * 3), wait=wait_fixed(3), reraise=True):
            with attempt:
                assert await is_storage_exists(ops_test, storage_id, include_detached=True)
                


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_app_garbage_ignorance(ops_test: OpsTest):
    """Test charm deploy in dirty environment with garbage storage"""
    async with ops_test.fast_forward():
        logger.info("checking garbage storage")
        garbage_storage = None
        for attempt in Retrying(stop=stop_after_delay(30 * 3), wait=wait_fixed(3), reraise=True):
            with attempt:
                garbage_storage = await get_any_deatached_storage(ops_test)

        logger.info("scale to 1")
        await scale_application(ops_test, DATABASE_APP_NAME, 1)

        # Reducing the update status frequency to speed up the triggering of deferred events.
        await ops_test.model.set_config({"update-status-hook-interval": "10s"})

        # Timeout is increeced due to k8s Init:CrashLoopBackOff status of postgresql pod
        logger.info("waiting for idle")
        await ops_test.model.wait_for_idle(status="active", timeout=2000)
        assert ops_test.model.applications[DATABASE_APP_NAME].units[0].workload_status == "active"

        logger.info("getting primary")
        primary_name = None
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3), reraise=True):
            with attempt:
                primary_name = await get_primary(ops_test, DATABASE_APP_NAME)
        
        logger.info("waiting for postgresql")
        for attempt in Retrying(stop=stop_after_delay(15 * 3), wait=wait_fixed(3), reraise=True):
            with attempt:
                assert await is_postgresql_ready(ops_test, primary_name)

        # Check that test database is not exists for duplicate application 
        logger.info("checking db")
        assert not await check_db(ops_test, DATABASE_APP_NAME, TEST_DATABASE_RELATION_NAME)

        logger.info("scale to 0")
        await scale_application(ops_test, DATABASE_APP_NAME, 0)


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
@pytest.mark.skipif(juju_major_version < 3, reason="Requires juju 3 or higher")
async def test_app_resources_conflicts_v3(ops_test: OpsTest):
    """Test application deploy in dirty environment with garbage storage from another application."""
    async with ops_test.fast_forward():
        logger.info("checking garbage storage")
        garbage_storage = None
        for attempt in Retrying(stop=stop_after_delay(30 * 3), wait=wait_fixed(3), reraise=True):
            with attempt:
                garbage_storage = await get_any_deatached_storage(ops_test)

        logger.info("deploying duplicate application with attached storage")
        await ops_test.model.deploy(
            DATABASE_APP_NAME,
            application_name=DUP_DATABASE_APP_NAME,
            num_units=1,
            channel="14/stable",
            series=CHARM_SERIES,
            attach_storage=[tag.storage(garbage_storage)],
            config={"profile": "testing"},
        )

        # Reducing the update status frequency to speed up the triggering of deferred events.
        await ops_test.model.set_config({"update-status-hook-interval": "10s"})

        logger.info("waiting for duplicate application to be blocked")
        try:
            await ops_test.model.wait_for_idle(
                apps=[DUP_DATABASE_APP_NAME], timeout=1000, status="blocked"
            )
        except TimeoutError:
            logger.info("Application is not in blocked state. Checking logs...")

        # Since application have postgresql db in storage from external application it should not be able to connect due to new password
        logger.info("checking operator password auth")
        assert not await check_password_auth(
            ops_test, ops_test.model.applications[DUP_DATABASE_APP_NAME].units[0].name
        )


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
@pytest.mark.skipif(juju_major_version != 2, reason="Requires juju 2")
async def test_app_resources_conflicts_v2(ops_test: OpsTest,):
    """Test application deploy in dirty environment with garbage storage from another application."""
    async with ops_test.fast_forward():
        logger.info("checking garbage storage")
        garbage_storage = None
        for attempt in Retrying(stop=stop_after_delay(30 * 3), wait=wait_fixed(3), reraise=True):
            with attempt:
                garbage_storage = await get_any_deatached_storage(ops_test)

        # Deploy duplicaate charm
        logger.info("deploying duplicate application")
        await ops_test.model.deploy(
            DATABASE_APP_NAME,
            application_name=DUP_DATABASE_APP_NAME,
            num_units=1,
            channel="14/stable",
            series=CHARM_SERIES,
            config={"profile": "testing"},
        )

        # Reducing the update status frequency to speed up the triggering of deferred events.
        await ops_test.model.set_config({"update-status-hook-interval": "10s"})

        logger.info("waiting for duplicate application to be blocked")
        try:
            await ops_test.model.wait_for_idle(
                apps=[DUP_DATABASE_APP_NAME], timeout=1000, status="blocked"
            )
        except TimeoutError:
            logger.info("Application is not in blocked state. Checking logs...")

        # Since application have postgresql db in storage from external application it should not be able to connect due to new password
        logger.info("checking operator password auth")
        assert not await check_password_auth(
            ops_test, ops_test.model.applications[DUP_DATABASE_APP_NAME].units[0].name
        )


