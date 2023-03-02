#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import ast
import logging
from typing import Dict, Tuple

import pytest as pytest
from pytest_operator.plugin import OpsTest

from tests.integration.helpers import DATABASE_APP_NAME, build_and_deploy

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
        assert len(ast.literal_eval(action.results["backup-list"])) == 1
        await ops_test.model.wait_for_idle(status="active", timeout=1000)
