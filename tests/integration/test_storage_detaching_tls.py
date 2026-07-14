#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
"""Regression test for the storage-detaching crash during TLS scale-down.

Reproduces the scenario from the field report: scaling down a postgresql-k8s
unit with TLS enabled failed the storage-detaching hook with an uncaught
``ssl.SSLError: [X509: NO_CERTIFICATE_OR_CRL_FOUND]`` when the peer CA bundle
was empty/corrupt at scale-down time, leaving the unit in ``error`` and the
data storage attached.

The crash lives in the single-kernel lib's ``_httpx_get_request``:
``load_verify_locations`` raises ``ssl.SSLError`` (not ``FileNotFoundError``)
for an existing-but-certless CA bundle, and the ``suppress(FileNotFoundError)``
guard did not catch it. The error escaped ``parallel_patroni_get_request`` ->
``cluster_status`` -> ``get_primary`` (which only catches ``RetryError``) and
crashed the hook. With the lib fix the guard also swallows ``ssl.SSLError``,
so the request degrades to unreachable and the hook completes; the unit is
removed cleanly and the application returns to active.
"""

import logging

import pytest
from pytest_operator.plugin import OpsTest

from .helpers import (
    CHARM_BASE_NOBLE,
    DATABASE_APP_NAME,
    build_and_deploy,
    check_tls,
    check_tls_patroni_api,
    scale_application,
)

logger = logging.getLogger(__name__)

tls_certificates_app_name = "self-signed-certificates"
tls_channel = "1/stable"
tls_config = {"ca-common-name": "Test CA"}
INITIAL_UNITS = 3


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest, charm) -> None:
    """Build and deploy three units of PostgreSQL."""
    await build_and_deploy(ops_test, charm, INITIAL_UNITS, wait_for_idle=False)


@pytest.mark.abort_on_fail
async def test_enable_tls(ops_test: OpsTest) -> None:
    """Enable TLS on the cluster (precondition for the storage-detaching crash)."""
    async with ops_test.fast_forward():
        await ops_test.model.deploy(
            tls_certificates_app_name,
            config=tls_config,
            channel=tls_channel,
            base=CHARM_BASE_NOBLE,
        )
        await ops_test.model.relate(
            f"{DATABASE_APP_NAME}:peer-certificates", f"{tls_certificates_app_name}:certificates"
        )
        await ops_test.model.relate(
            f"{DATABASE_APP_NAME}:client-certificates", f"{tls_certificates_app_name}:certificates"
        )
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME], status="active", timeout=1000, raise_on_error=False
        )
        for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
            assert await check_tls(ops_test, unit.name, enabled=True)
            assert await check_tls_patroni_api(ops_test, unit.name, enabled=True)


async def test_scale_down_with_tls(ops_test: OpsTest) -> None:
    """Scale down by one unit with TLS enabled; the hook must not crash.

    Under the unfixed lib the departing unit's storage-detaching hook crashed on
    an empty/corrupt peer CA bundle (``ssl.SSLError``), leaving the unit in
    ``error`` with its data storage attached and the application unable to
    return to active. With the fix the hook degrades gracefully and the unit is
    removed cleanly.
    """
    # scale_application waits for the app to return to active with exactly
    # INITIAL_UNITS - 1 units; the pre-fix crash strands the departing unit in
    # error and this wait raises on timeout.
    await scale_application(ops_test, DATABASE_APP_NAME, INITIAL_UNITS - 1)

    # Belt-and-suspenders: no surviving unit should be in error.
    for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
        assert unit.workload_status != "error", (
            f"unit {unit.name} is in error after TLS scale-down; "
            "storage-detaching hook likely crashed on an empty CA bundle"
        )
