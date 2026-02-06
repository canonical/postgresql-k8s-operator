#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import pytest
from pytest_operator.plugin import OpsTest

from .backup_helpers import pitr_backup_operations
from .conftest import ConnectionInformation

logger = logging.getLogger(__name__)

S3_INTEGRATOR_APP_NAME = "s3-integrator"

backup_id, value_before_backup, value_after_backup = "", None, None


@pytest.fixture(scope="session")
def cloud_credentials(microceph: ConnectionInformation) -> dict[str, str]:
    """Read cloud credentials."""
    return {
        "access-key": microceph.access_key_id,
        "secret-key": microceph.secret_access_key,
    }


@pytest.fixture(scope="session")
def cloud_configs(microceph: ConnectionInformation):
    return {
        "endpoint": f"https://{microceph.host}",
        "bucket": microceph.bucket,
        "path": "/pg",
        "region": "",
        "s3-uri-style": "path",
        "tls-ca-chain": microceph.cert,
    }


@pytest.mark.abort_on_fail
async def test_pitr_backup_ceph(
    ops_test: OpsTest, cloud_configs, cloud_credentials, charm
) -> None:
    """Build and deploy two units of PostgreSQL in AWS and then test PITR backup and restore actions."""
    await pitr_backup_operations(
        ops_test,
        charm,
        S3_INTEGRATOR_APP_NAME,
        None,
        None,
        cloud_credentials,
        "ceph",
        cloud_configs,
    )
