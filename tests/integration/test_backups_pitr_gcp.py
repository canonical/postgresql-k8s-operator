#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import pytest
from pytest_operator.plugin import OpsTest

from .backup_helpers import pitr_backup_operations
from .conftest import GCP

CANNOT_RESTORE_PITR = "cannot restore PITR, juju debug-log for details"
S3_INTEGRATOR_APP_NAME = "s3-integrator"
tls_certificates_app_name = "self-signed-certificates"
tls_channel = "1/stable"

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_pitr_backup_gcp(
    ops_test: OpsTest, charm, gcp_cloud_configs: tuple[dict, dict]
) -> None:
    """Build and deploy two units of PostgreSQL in GCP and then test PITR backup and restore actions."""
    config = gcp_cloud_configs[0]
    credentials = gcp_cloud_configs[1]
    cloud = GCP.lower()

    await pitr_backup_operations(
        ops_test,
        charm,
        S3_INTEGRATOR_APP_NAME,
        tls_certificates_app_name,
        tls_channel,
        credentials,
        cloud,
        config,
    )
