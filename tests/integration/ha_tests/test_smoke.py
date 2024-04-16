#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import psycopg2
import pytest
import os
import requests
from psycopg2 import sql
import subprocess
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_attempt, stop_after_delay, wait_fixed
from time import sleep

from ..helpers import (
    CHARM_SERIES,
    get_unit_address,
    APPLICATION_NAME,
    DATABASE_APP_NAME,
    get_primary,
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
)

TEST_DATABASE_RELATION_NAME = "test_database"
DUP_DATABASE_APP_NAME = DATABASE_APP_NAME + "2"

logger = logging.getLogger(__name__)


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_app_removal(ops_test: OpsTest):
    """Test all recoureces is removed after application removal"""

    env = os.environ
    env["KUBECONFIG"] = os.path.expanduser("~/.kube/config")
    print(f"Model Name: {DATABASE_APP_NAME}")

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

        assert is_pods_exists(ops_test, primary_name)

        storage_id = await get_storage_id(ops_test, primary_name)

        assert await is_storage_exists(ops_test, storage_id)

        await ops_test.model.remove_application(DATABASE_APP_NAME, block_until_done=True, destroy_storage=True)

        # Check if storage removed after application removal
        assert not await is_storage_exists(ops_test, storage_id)

        # Check if pods are removed after application removal
        assert not is_pods_exists(ops_test, primary_name)



@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_app_force_removal(ops_test: OpsTest):
    """Remove unit with force while storage is alive"""

    # Deploy the charm.
    async with ops_test.fast_forward():
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

        
        await ops_test.model.wait_for_idle(status="active", timeout=1000)

        assert ops_test.model.applications[DATABASE_APP_NAME].units[0].workload_status == "active"

        primary_name = None
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
            with attempt:
                primary_name = await get_primary(ops_test, DATABASE_APP_NAME)

        assert primary_name is not None
    
        assert await is_postgresql_ready(ops_test, primary_name)

        storage_id = await get_storage_id(ops_test, primary_name)

        assert await is_storage_exists(ops_test, storage_id)

        assert is_pods_exists(ops_test, primary_name)

        # Create test database to check there is no resouces conflicts
        await create_db(ops_test, DATABASE_APP_NAME, TEST_DATABASE_RELATION_NAME)

        # Remove application witout storage removal
        await ops_test.model.destroy_unit(primary_name, force=True, destroy_storage=False, max_wait=1500)

        # Storage will remain with deatached status
        assert await is_storage_exists(ops_test, storage_id)

        # Pod will remain
        assert is_pods_exists(ops_test, primary_name)


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_app_garbage_ignorance(ops_test: OpsTest):
    """Test charm deploy in dirty environment with garbage storage"""
    async with ops_test.fast_forward():
        garbadge_storage = None
        for attempt in Retrying(stop=stop_after_delay(30 * 3), wait=wait_fixed(3)):
            with attempt:
                garbadge_storage = await get_any_deatached_storage(ops_test)
                assert garbadge_storage is not None

        assert garbadge_storage is not None

        await ops_test.model.applications[APPLICATION_NAME].add_unit(1)

        # Reducing the update status frequency to speed up the triggering of deferred events.
        await ops_test.model.set_config({"update-status-hook-interval": "10s"})

        # Timeout is increeced due to k8s Init:CrashLoopBackOff status of postgresql pod
        await ops_test.model.wait_for_idle(status="active", timeout=2000)

        assert ops_test.model.applications[DATABASE_APP_NAME].units[0].workload_status == "active"

        primary_name = None
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
            with attempt:
                primary_name = await get_primary(ops_test, DATABASE_APP_NAME)

        assert primary_name is not None
        
        assert await is_postgresql_ready(ops_test, primary_name)

        # Check that test database is not exists for duplicate application 
        assert not await check_db(ops_test, APPLICATION_NAME, TEST_DATABASE_RELATION_NAME)

        await ops_test.model.destroy_unit(primary_name, destroy_storage=False, max_wait=1500)


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_app_recoures_conflicts(ops_test: OpsTest, charm: str):
    """Test application deploy in dirty environment with garbage storage from another application """     
    async with ops_test.fast_forward():
        garbadge_storage = None
        for attempt in Retrying(stop=stop_after_delay(30 * 3), wait=wait_fixed(3)):
            with attempt:
                garbadge_storage = await get_any_deatached_storage(ops_test)
                assert garbadge_storage is not None

        assert garbadge_storage is not None

        # Deploy duplicaate charm
        await ops_test.model.deploy(
            DATABASE_APP_NAME,
            application_name=DUP_DATABASE_APP_NAME,
            num_units=1,
            channel="14/stable",
            series=CHARM_SERIES,
        )

        try:
            await ops_test.model.wait_for_idle(apps=[DUP_DATABASE_APP_NAME], timeout=500, status="blocked")
        except (TimeoutError) as e:
            logger.info(f"Application is not in blocked state. Checking logs...")

        # Since application have postgresql db in storage from external application it should not be able to connect due to new password
        assert not await check_password_auth(ops_test, ops_test.model.applications[DUP_DATABASE_APP_NAME].units[0].name)

