#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
from typing import Dict, Tuple

import pytest as pytest
from pytest_operator.plugin import OpsTest

from tests.integration.helpers import (
    DATABASE_APP_NAME,
    build_and_deploy,
    db_connect,
    get_password,
    get_unit_address,
)

S3_INTEGRATOR_APP_NAME = "s3-integrator"

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_backup_and_restore(ops_test: OpsTest, cloud_configs: Tuple[Dict, Dict]) -> None:
    """Build and deploy one unit of PostgreSQL and then test the backup and restore actions."""
    # Deploy S3 Integrator.
    await ops_test.model.deploy(S3_INTEGRATOR_APP_NAME, channel="edge")

    for cloud, config in cloud_configs[0].items():
        # Deploy and relate PostgreSQL to S3 integrator (one database app for each cloud for now
        # as archivo_mode is disabled after restoring the backup).
        database_app_name = f"{DATABASE_APP_NAME}-{cloud.lower()}"
        await build_and_deploy(
            ops_test, 1, database_app_name=database_app_name, wait_for_idle=False
        )
        await ops_test.model.relate(database_app_name, S3_INTEGRATOR_APP_NAME)

        # Configure and set access and secret keys.
        logger.info(f"configuring S3 integrator for {cloud}")
        await ops_test.model.applications[S3_INTEGRATOR_APP_NAME].set_config(config)
        action = await ops_test.model.units.get(f"{S3_INTEGRATOR_APP_NAME}/0").run_action(
            "sync-s3-credentials",
            **cloud_configs[1][cloud],
        )
        await action.wait()
        await ops_test.model.wait_for_idle(
            apps=[database_app_name, S3_INTEGRATOR_APP_NAME], status="active", timeout=1000
        )

        # Write some data.
        password = await get_password(ops_test, database_app_name=database_app_name)
        address = await get_unit_address(ops_test, f"{database_app_name}/0")
        logger.info("creating a table in the database")
        with db_connect(host=address, password=password) as connection:
            connection.autocommit = True
            connection.cursor().execute(
                "CREATE TABLE IF NOT EXISTS backup_table_1 (test_collumn INT );"
            )
        connection.close()

        # Run the "create backup" action.
        logger.info("creating a backup")
        action = await ops_test.model.units.get(f"{database_app_name}/0").run_action(
            "create-backup"
        )
        await action.wait()
        logger.info(f"backup results: {action.results}")
        await ops_test.model.wait_for_idle(
            apps=[database_app_name, S3_INTEGRATOR_APP_NAME], status="active", timeout=1000
        )

        # Run the "list backups" action.
        logger.info("listing the available backups")
        action = await ops_test.model.units.get(f"{database_app_name}/0").run_action(
            "list-backups"
        )
        await action.wait()
        backups = action.results["backups"]
        assert backups, "backups not outputted"
        await ops_test.model.wait_for_idle(status="active", timeout=1000)

        # Write some data.
        logger.info("creating a second table in the database")
        with db_connect(host=address, password=password) as connection:
            connection.autocommit = True
            connection.cursor().execute("CREATE TABLE backup_table_2 (test_collumn INT );")
        connection.close()

        # Run the "restore backup" action.
        logger.info("restoring the backup")
        most_recent_backup = backups.split("\n")[-1]
        backup_id = most_recent_backup.split()[0]
        action = await ops_test.model.units.get(f"{database_app_name}/0").run_action(
            "restore", **{"backup-id": backup_id}
        )
        await action.wait()
        logger.info(f"restore results: {action.results}")

        # Wait for the backup to complete.
        async with ops_test.fast_forward():
            await ops_test.model.wait_for_idle(status="active", timeout=1000)

        # Check that the backup was correctly restored by having only the first created table.
        logger.info("checking that the backup was correctly restored")
        with db_connect(
            host=address, password=password
        ) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS (SELECT FROM information_schema.tables"
                " WHERE table_schema = 'public' AND table_name = 'backup_table_1');"
            )
            assert cursor.fetchone()[
                0
            ], "backup wasn't correctly restored: table 'backup_table_1' doesn't exist"
            cursor.execute(
                "SELECT EXISTS (SELECT FROM information_schema.tables"
                " WHERE table_schema = 'public' AND table_name = 'backup_table_2');"
            )
            assert not cursor.fetchone()[
                0
            ], "backup wasn't correctly restored: table 'backup_table_2' exists"
        connection.close()

        # Remove the database app.
        await ops_test.model.applications[database_app_name].remove()
