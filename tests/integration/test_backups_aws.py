#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_attempt, wait_exponential

from .backup_helpers import backup_operations
from .conftest import AWS
from .helpers import (
    DATABASE_APP_NAME,
    db_connect,
    get_password,
    get_primary,
    get_unit_address,
    scale_application,
    switchover,
)
from .juju_ import juju_major_version

ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE = "the S3 repository has backups from another cluster"
FAILED_TO_ACCESS_CREATE_BUCKET_ERROR_MESSAGE = (
    "failed to access/create the bucket, check your S3 settings"
)
FAILED_TO_INITIALIZE_STANZA_ERROR_MESSAGE = "failed to initialize stanza, check your S3 settings"
S3_INTEGRATOR_APP_NAME = "s3-integrator"
if juju_major_version < 3:
    tls_certificates_app_name = "tls-certificates-operator"
    tls_channel = "legacy/stable"
    tls_config = {"generate-self-signed-certificates": "true", "ca-common-name": "Test CA"}
else:
    tls_certificates_app_name = "self-signed-certificates"
    tls_channel = "1/stable"
    tls_config = {"ca-common-name": "Test CA"}

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_backup_aws(ops_test: OpsTest, charm, aws_cloud_configs: tuple[dict, dict]) -> None:
    """Build and deploy two units of PostgreSQL in AWS and then test the backup and restore actions."""
    config = aws_cloud_configs[0]
    credentials = aws_cloud_configs[1]

    await backup_operations(
        ops_test,
        charm,
        S3_INTEGRATOR_APP_NAME,
        tls_certificates_app_name,
        tls_config,
        tls_channel,
        credentials,
        AWS,
        config,
    )
    database_app_name = f"{DATABASE_APP_NAME}-aws"

    async with ops_test.fast_forward():
        logger.info("removing the TLS relation")
        await ops_test.model.applications[database_app_name].remove_relation(
            f"{database_app_name}:certificates",
            f"{tls_certificates_app_name}:certificates",
        )

        new_unit_name = f"{database_app_name}/1"

        # Scale up to be able to test primary and leader being different.
        async with ops_test.fast_forward():
            await scale_application(ops_test, database_app_name, 2)

        logger.info("ensuring that the replication is working correctly")
        address = await get_unit_address(ops_test, new_unit_name)
        password = await get_password(ops_test, database_app_name=database_app_name)
        patroni_password = await get_password(
            ops_test, "patroni", database_app_name=database_app_name
        )
        for attempt in Retrying(
            stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
        ):
            with attempt:
                with (
                    db_connect(host=address, password=password) as connection,
                    connection.cursor() as cursor,
                ):
                    cursor.execute(
                        "SELECT EXISTS (SELECT FROM information_schema.tables"
                        " WHERE table_schema = 'public' AND table_name = 'backup_table_1');"
                    )
                    assert cursor.fetchone()[0], (
                        f"replication isn't working correctly: table 'backup_table_1' doesn't exist in {new_unit_name}"
                    )
                    cursor.execute(
                        "SELECT EXISTS (SELECT FROM information_schema.tables"
                        " WHERE table_schema = 'public' AND table_name = 'backup_table_2');"
                    )
                    assert not cursor.fetchone()[0], (
                        f"replication isn't working correctly: table 'backup_table_2' exists in {new_unit_name}"
                    )
                connection.close()

        old_primary = await get_primary(ops_test, database_app_name)
        logger.info(f"performing a switchover from {old_primary} to {new_unit_name}")
        await switchover(ops_test, old_primary, patroni_password, new_unit_name)

        logger.info("checking that the primary unit has changed")
        primary = await get_primary(ops_test, database_app_name)
        for attempt in Retrying(
            stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30)
        ):
            with attempt:
                assert primary == new_unit_name

        # Ensure stanza is working correctly.
        logger.info("listing the available backups to ensure that the stanza is working correctly")
        action = await ops_test.model.units.get(new_unit_name).run_action("list-backups")
        await action.wait()
        backups = action.results.get("backups")
        assert backups, "backups not outputted"

        await ops_test.model.wait_for_idle(status="active", timeout=1000)

    # Remove the database app.
    await ops_test.model.remove_application(database_app_name)
    await ops_test.model.block_until(
        lambda: database_app_name not in ops_test.model.applications, timeout=1000
    )
    # Remove the TLS operator.
    await ops_test.model.remove_application(tls_certificates_app_name)
    await ops_test.model.block_until(
        lambda: tls_certificates_app_name not in ops_test.model.applications, timeout=1000
    )
