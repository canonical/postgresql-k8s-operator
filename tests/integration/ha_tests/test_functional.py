#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import uuid
from typing import Dict, Tuple

import boto3
import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_attempt, stop_after_delay, wait_exponential, wait_fixed

from ..helpers import (
    DATABASE_APP_NAME,
    build_and_deploy,
    construct_endpoint,
)
from .helpers import (
    check_db,
    check_graceful_shutdown,
    check_success_recovery,
    create_db,
    is_postgresql_ready,
    restart_pod,
)

TEST_DATABASE_NAME = "test_database"
DUP_APPLICATION_NAME = "postgres-test-dup"
S3_INTEGRATOR_APP_NAME = "s3-integrator"

logger = logging.getLogger(__name__)

AWS = "AWS"


@pytest.fixture(scope="module")
async def cloud_configs(ops_test: OpsTest, github_secrets) -> None:
    # Define some configurations and credentials.
    configs = {
        AWS: {
            "endpoint": "https://s3.amazonaws.com",
            "bucket": "data-charms-testing",
            "path": f"/postgresql-vm/{uuid.uuid1()}",
            "region": "us-east-1",
        },
    }
    credentials = {
        AWS: {
            "access-key": github_secrets["AWS_ACCESS_KEY"],
            "secret-key": github_secrets["AWS_SECRET_KEY"],
        },
    }
    yield configs, credentials
    # Delete the previously created objects.
    logger.info("deleting the previously created backups")
    for cloud, config in configs.items():
        session = boto3.session.Session(
            aws_access_key_id=credentials[cloud]["access-key"],
            aws_secret_access_key=credentials[cloud]["secret-key"],
            region_name=config["region"],
        )
        s3 = session.resource(
            "s3", endpoint_url=construct_endpoint(config["endpoint"], config["region"])
        )
        bucket = s3.Bucket(config["bucket"])
        for bucket_object in bucket.objects.filter(Prefix=config["path"].lstrip("/")):
            bucket_object.delete()


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_instance_graceful_restart(ops_test: OpsTest) -> None:
    """Test graceful restart of a service."""
    async with ops_test.fast_forward():
        # Deploy the charm.
        logger.info("deploying charm")
        await build_and_deploy(
            ops_test, 1, database_app_name=DATABASE_APP_NAME, wait_for_idle=False
        )

        primary_name = ops_test.model.applications[DATABASE_APP_NAME].units[0].name

        logger.info("waiting for postgresql")
        for attempt in Retrying(
            stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
        ):
            with attempt:
                assert await is_postgresql_ready(ops_test, primary_name)

        logger.info("restarting pod")
        restart_pod(ops_test, primary_name.replace("/", "-"))

        logger.info("waiting for idle")
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME], status="active", timeout=1500, raise_on_error=False
        )
        assert ops_test.model.applications[DATABASE_APP_NAME].units[0].workload_status == "active"

        logger.info("check graceful shutdown")
        for attempt in Retrying(stop=stop_after_delay(15 * 3), wait=wait_fixed(3), reraise=True):
            with attempt:
                assert await check_graceful_shutdown(ops_test, primary_name)

        logger.info("check success recovery")
        assert await check_success_recovery(ops_test, primary_name)

        logger.info("remove application")
        for attempt in Retrying(stop=stop_after_delay(15 * 3), wait=wait_fixed(3), reraise=True):
            with attempt:
                await ops_test.model.remove_application(DATABASE_APP_NAME, block_until_done=True)


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_instance_forceful_restart(ops_test: OpsTest) -> None:
    """Test forceful restart of a service."""
    async with ops_test.fast_forward():
        # Deploy the charm.
        logger.info("deploying charm")
        await build_and_deploy(
            ops_test, 1, database_app_name=DATABASE_APP_NAME, wait_for_idle=False
        )

        primary_name = ops_test.model.applications[DATABASE_APP_NAME].units[0].name

        logger.info("waiting for postgresql")
        for attempt in Retrying(
            stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
        ):
            with attempt:
                assert await is_postgresql_ready(ops_test, primary_name)

        logger.info("restarting pod with force")
        restart_pod(ops_test, primary_name.replace("/", "-"), force=True)

        logger.info("waiting for idle")
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME], status="active", timeout=1500, raise_on_error=False
        )
        assert ops_test.model.applications[DATABASE_APP_NAME].units[0].workload_status == "active"

        logger.info("check forceful shutdown")
        for attempt in Retrying(stop=stop_after_delay(15 * 3), wait=wait_fixed(3), reraise=True):
            with attempt:
                assert not await check_graceful_shutdown(ops_test, primary_name)

        logger.info("check success recovery")
        assert await check_success_recovery(ops_test, primary_name)

        logger.info("remove application")
        for attempt in Retrying(stop=stop_after_delay(15 * 3), wait=wait_fixed(3), reraise=True):
            with attempt:
                await ops_test.model.remove_application(DATABASE_APP_NAME, block_until_done=True)


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_instance_backup_with_restart(
    ops_test: OpsTest, cloud_configs: Tuple[Dict, Dict], charm
) -> None:
    """Test instance backup after recovery."""
    async with ops_test.fast_forward():
        logger.info("deploying s3")
        await ops_test.model.deploy(S3_INTEGRATOR_APP_NAME)

        for cloud, config in cloud_configs[0].items():
            # Deploy and relate PostgreSQL to S3 integrator (one database app for each cloud for now
            # as archive_mode is disabled after restoring the backup)
            logger.info("deploying charm")
            await build_and_deploy(
                ops_test, 1, database_app_name=DATABASE_APP_NAME, wait_for_idle=False
            )

            logger.info("relate s3")
            await ops_test.model.relate(DATABASE_APP_NAME, S3_INTEGRATOR_APP_NAME)

            # Configure and set access and secret keys.
            logger.info(f"configuring S3 integrator for {cloud}")
            await ops_test.model.applications[S3_INTEGRATOR_APP_NAME].set_config(config)
            action = await ops_test.model.units.get(f"{S3_INTEGRATOR_APP_NAME}/0").run_action(
                "sync-s3-credentials",
                **cloud_configs[1][cloud],
            )
            await action.wait()
            await ops_test.model.wait_for_idle(
                apps=[DATABASE_APP_NAME, S3_INTEGRATOR_APP_NAME], status="active", timeout=1500
            )

            primary_unit = ops_test.model.applications[DATABASE_APP_NAME].units[0]
            primary_name = primary_unit.name

            # Write some data.
            logger.info("write data before backup")
            await create_db(ops_test, DATABASE_APP_NAME, TEST_DATABASE_NAME)

            # Run the "create backup" action.
            logger.info("creating a backup")
            action = await ops_test.model.units.get(primary_name).run_action("create-backup")
            await action.wait()
            backup_status = action.results.get("backup-status")
            assert backup_status, "backup hasn't succeeded"
            await ops_test.model.wait_for_idle(
                apps=[DATABASE_APP_NAME, S3_INTEGRATOR_APP_NAME], status="active", timeout=1000
            )

            # Run the "list backups" action.
            logger.info("listing the available backups")
            action = await ops_test.model.units.get(primary_name).run_action("list-backups")
            await action.wait()
            backups = action.results.get("backups")
            assert backups, "backups not outputted"
            await ops_test.model.wait_for_idle(status="active", timeout=1500)

            # Write some data.
            logger.info("write data after backup")
            await create_db(ops_test, DATABASE_APP_NAME, TEST_DATABASE_NAME + "_dup")

            logger.info("restarting pod with force")
            restart_pod(ops_test, primary_name.replace("/", "-"), force=True)

            logger.info("waiting for idle")
            await ops_test.model.wait_for_idle(
                apps=[DATABASE_APP_NAME], status="active", timeout=1500, raise_on_error=False
            )
            assert (
                ops_test.model.applications[DATABASE_APP_NAME].units[0].workload_status == "active"
            )

            # Run the "restore backup" action.
            for attempt in Retrying(
                stop=stop_after_delay(15 * 3), wait=wait_fixed(3), reraise=True
            ):
                with attempt:
                    logger.info("restoring the backup")
                    most_recent_backup = backups.split("\n")[-1]
                    backup_id = most_recent_backup.split()[0]
                    action = await primary_unit.run_action("restore", **{"backup-id": backup_id})
                    await action.wait()
                    restore_status = action.results.get("restore-status")
                    assert restore_status, "restore hasn't succeeded"

            # Wait for the restore to complete.
            logger.info("wait for restore")
            await ops_test.model.wait_for_idle(status="active", timeout=1500)

            logger.info("checking data consistency")
            assert await check_db(ops_test, DATABASE_APP_NAME, TEST_DATABASE_NAME)
            assert not await check_db(ops_test, DATABASE_APP_NAME, TEST_DATABASE_NAME + "_dup")

            logger.info("remove application")
            await ops_test.model.remove_application(DATABASE_APP_NAME, block_until_done=True)
