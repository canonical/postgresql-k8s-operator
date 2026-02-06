#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import pytest
from pytest_operator.plugin import OpsTest

from .backup_helpers import pitr_backup_operations
from .conftest import AWS
from .juju_ import juju_major_version

CANNOT_RESTORE_PITR = "cannot restore PITR, juju debug-log for details"
S3_INTEGRATOR_APP_NAME = "s3-integrator"
if juju_major_version < 3:
    tls_certificates_app_name = "tls-certificates-operator"
    tls_channel = "legacy/stable"
    tls_base = "ubuntu@22.04"
    tls_config = {"generate-self-signed-certificates": "true", "ca-common-name": "Test CA"}
else:
    tls_certificates_app_name = "self-signed-certificates"
    tls_channel = "1/stable"
    tls_base = "ubuntu@24.04"
    tls_config = {"ca-common-name": "Test CA"}

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_pitr_backup_aws(
    ops_test: OpsTest, charm, aws_cloud_configs: tuple[dict, dict]
) -> None:
    """Build and deploy two units of PostgreSQL in AWS and then test PITR backup and restore actions."""
    config = aws_cloud_configs[0]
    credentials = aws_cloud_configs[1]
    cloud = AWS.lower()

    await pitr_backup_operations(
        ops_test,
        charm,
        S3_INTEGRATOR_APP_NAME,
        tls_certificates_app_name,
        tls_config,
        tls_channel,
        tls_base,
        credentials,
        cloud,
        config,
    )
