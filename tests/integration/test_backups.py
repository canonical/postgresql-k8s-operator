#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import ast
import logging
import os
import uuid

import pytest as pytest
from pytest_operator.plugin import OpsTest

from tests.integration.helpers import DATABASE_APP_NAME, build_and_deploy

AWS = "AWS"
GCP = "GCP"
S3_INTEGRATOR_APP_NAME = "s3-integrator"

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
@pytest.mark.backup_tests
async def test_backup(ops_test: OpsTest) -> None:
    """Build and deploy three unit of PostgreSQL and then test the backup action."""
    # Define some configurations.
    configs = {
        AWS: {
            "endpoint": "s3.amazonaws.com",
            "bucket": "canonical-postgres",
            "path": f"/{uuid.uuid1()}",
            "region": "us-east-2",
        },
        GCP: {
            "endpoint": "https://storage.googleapis.com",
            "bucket": "data-charms-testing",
            "path": f"/postgresql-k8s/{uuid.uuid1()}",
            "region": "",
        },
    }
    credentials = {
        AWS: {
            "access-key": os.environ.get("AWS_ACCESS_KEY"),
            "secret-key": os.environ.get("AWS_SECRET_KEY"),
        },
        GCP: {
            "access-key": os.environ.get("GCP_ACCESS_KEY"),
            "secret-key": os.environ.get("GCP_SECRET_KEY"),
        },
    }

    # Deploy PostgreSQL and S3 Integrator.
    await build_and_deploy(ops_test, 1, wait_for_idle=False)
    await ops_test.model.deploy(S3_INTEGRATOR_APP_NAME, channel="edge")

    # Relate PostgreSQL to S3 integrator.
    await ops_test.model.relate(DATABASE_APP_NAME, S3_INTEGRATOR_APP_NAME)

    for cloud, config in configs.items():
        # Configure and set access and secret keys.
        await ops_test.model.applications[S3_INTEGRATOR_APP_NAME].set_config(config)
        action = await ops_test.model.units.get(f"{S3_INTEGRATOR_APP_NAME}/0").run_action(
            "sync-s3-credentials",
            **credentials[cloud],
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
