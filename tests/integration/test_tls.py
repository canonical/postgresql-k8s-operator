#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import pytest
from pytest_operator.plugin import OpsTest

from tests.helpers import METADATA
from tests.integration.helpers import is_tls_enabled

APP_NAME = METADATA["name"]
TLS_CERTIFICATES_APP_NAME = "tls-certificates-operator"


@pytest.mark.abort_on_fail
@pytest.mark.tls_tests
@pytest.mark.skip_if_deployed
async def test_deploy_active(ops_test: OpsTest):
    """Build the charm and deploy it."""
    charm = await ops_test.build_charm(".")
    async with ops_test.fast_forward():
        await ops_test.model.deploy(
            charm,
            resources={
                "postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"]
            },
            application_name=APP_NAME,
            num_units=3,
            trust=True,
        )
        await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)


@pytest.mark.tls_tests
async def test_tls(ops_test: OpsTest):
    """Test TLS enabled on PostgreSQL instance."""
    async with ops_test.fast_forward():
        # Deploy TLS Certificates operator.
        config = {"generate-self-signed-certificates": "true", "ca-common-name": "Test CA"}
        await ops_test.model.deploy(TLS_CERTIFICATES_APP_NAME, channel="edge", config=config)
        await ops_test.model.wait_for_idle(
            apps=[TLS_CERTIFICATES_APP_NAME], status="active", timeout=1000
        )

        # Relate it to the PostgreSQL charm.
        await ops_test.model.relate(APP_NAME, TLS_CERTIFICATES_APP_NAME)
        await ops_test.model.wait_for_idle(status="active", timeout=1000)

    # Wait for all units enabling TLS.
    for unit in ops_test.model.applications[APP_NAME].units:
        assert await is_tls_enabled(ops_test, unit.name)
