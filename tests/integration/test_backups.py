#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import ast
import logging
from typing import Dict, Tuple

import pytest as pytest
from pytest_operator.plugin import OpsTest

from tests.integration.helpers import (
    DATABASE_APP_NAME,
    build_and_deploy,
    db_connect,
    get_password,
    get_primary,
    get_unit_address,
)

S3_INTEGRATOR_APP_NAME = "s3-integrator"

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_backup(ops_test: OpsTest, cloud_configs: Tuple[Dict, Dict]) -> None:
    """Build and deploy one unit of PostgreSQL and then test the backup action."""
    # Deploy PostgreSQL and S3 Integrator.
    await build_and_deploy(ops_test, 1, wait_for_idle=False)
    await ops_test.model.deploy(S3_INTEGRATOR_APP_NAME, channel="edge")

    # Relate PostgreSQL to S3 integrator.
    await ops_test.model.relate(DATABASE_APP_NAME, S3_INTEGRATOR_APP_NAME)

    for cloud, config in cloud_configs[0].items():
        # Configure and set access and secret keys.
        await ops_test.model.applications[S3_INTEGRATOR_APP_NAME].set_config(config)
        action = await ops_test.model.units.get(f"{S3_INTEGRATOR_APP_NAME}/0").run_action(
            "sync-s3-credentials",
            **cloud_configs[1][cloud],
        )
        await action.wait()
        await ops_test.model.wait_for_idle(status="active", timeout=1000)

        # Write some data.
        primary = await get_primary(ops_test)
        password = await get_password(ops_test)
        address = await get_unit_address(ops_test, primary)
        logger.info(f"connecting to primary {primary} on {address}")
        with db_connect(host=address, password=password) as connection:
            connection.autocommit = True
            connection.cursor().execute("CREATE TABLE backup_table_1 (test_collumn INT );")
        connection.close()

        # Run the "create backup" action.
        action = await ops_test.model.units.get(f"{DATABASE_APP_NAME}/0").run_action(
            "create-backup"
        )
        await action.wait()
        logger.info(f"backup results: {action.results}")
        await ops_test.model.wait_for_idle(status="active", timeout=1000)

        # Run the "list backups" action.
        action = await ops_test.model.units.get(f"{DATABASE_APP_NAME}/0").run_action(
            "list-backups"
        )
        await action.wait()
        logger.info(f"list backups results: {action.results}")
        backup_list = ast.literal_eval(action.results["backup-list"])
        logger.info(backup_list)
        assert len(backup_list) == 1
        await ops_test.model.wait_for_idle(status="active", timeout=1000)

        # Write some data.
        logger.info(f"connecting to primary {primary} on {address}")
        with db_connect(host=address, password=password) as connection:
            connection.autocommit = True
            connection.cursor().execute("CREATE TABLE backup_table_2 (test_collumn INT );")
        connection.close()

        # Run the "restore backup" action.
        action = await ops_test.model.units.get(f"{DATABASE_APP_NAME}/0").run_action(
            "restore", **{"backup-id": backup_list[0]}
        )
        await action.wait()
        logger.info(f"restore results: {action.results}")

        # Wait for the backup to complete.
        async with ops_test.fast_forward():
            await ops_test.model.wait_for_idle(status="active", timeout=1000)

        # logger.info(f"connecting to primary {primary} on {address}")
        # with db_connect(host=address, password=password) as connection:
        #     connection.autocommit = True
        #     connection.cursor().execute("CREATE TABLE backup_table_2 (test_collumn INT );")
        # connection.close()
