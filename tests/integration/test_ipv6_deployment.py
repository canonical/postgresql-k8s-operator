# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for the PostgreSQL K8s charm across address-family scenarios.

The pg_hba rules the charm renders must match clients from every address family
the deployment uses: with IPv4-only rules, Patroni's own bootstrap connections
coming from an IPv6 address are rejected with FATAL 28000 and the bootstrap
loops in a wipe-and-restart cycle (canonical/postgresql-operator#1928, which
the bundled single-kernel library fixes by rendering IPv6 twins).

Scenarios are selected with the PG_IP_FAMILY environment variable:

The k8s snap (1.35.x) bootstrap config schema is single-CIDR
(`UserFacingClusterConfig.PodCIDR/ServiceCIDR` are strings), so the ipv6/dual
scenarios require a cluster bootstrapped with an IPv6 CIDR (the spread task
bootstraps one when the platform supports it) and skip otherwise. An
IPv6-only pod network additionally requires the Patroni REST API to accept
IPv6 connections, which the charm does not do yet.
"""

import os

import pytest
from pytest_operator.plugin import OpsTest

from .helpers import (
    ACTUAL_PGDATA_PATH,
    build_and_deploy,
    get_unit_address,
    run_command_on_unit,
)
from .helpers import (
    DATABASE_APP_NAME as APP_NAME,
)

IP_FAMILY = os.environ.get("PG_IP_FAMILY", "ipv4")
PG_HBA_PATH = f"{ACTUAL_PGDATA_PATH}/pg_hba.conf"
V6_SUBNET = os.environ.get("PG_IPV6_SUBNET", "fd42:1928:642::/64")


@pytest.mark.abort_on_fail
async def test_deploy(ops_test: OpsTest, charm):
    """Deploy the charm and wait for it to reach active/idle."""
    async with ops_test.fast_forward():
        await build_and_deploy(ops_test, charm, 1, APP_NAME)
    assert ops_test.model.applications[APP_NAME].units[0].workload_status == "active"


@pytest.mark.abort_on_fail
async def test_pg_hba_renders_ipv6_rules(ops_test: OpsTest):
    """The rendered pg_hba inside the workload must contain the IPv6 twins.

    These rules are what allow Patroni's own connections (and any client)
    from an IPv6 address to authenticate; without them the bootstrap loop
    documented in #1928 reproduces on any deployment with IPv6 addresses.
    """
    count = await run_command_on_unit(
        ops_test, f"{APP_NAME}/0", f"grep -c '::/0' {PG_HBA_PATH}"
    )
    assert int(count) >= 1, "no IPv6 pg_hba rules rendered in the workload"


@pytest.mark.abort_on_fail
async def test_address_family_matches_scenario(ops_test: OpsTest):
    """The unit address family must match the scenario under test."""
    address = await get_unit_address(ops_test, f"{APP_NAME}/0")

    if IP_FAMILY == "ipv4":
        assert ":" not in address, f"expected an IPv4 unit address, got {address}"
    elif IP_FAMILY == "ipv6":
        assert ":" in address, f"expected an IPv6 unit address, got {address}"
    # dual: either family is acceptable; both must be usable, which the
    # primary-reachability test below covers.


@pytest.mark.abort_on_fail
async def test_primary_is_reachable(ops_test: OpsTest):
    """The Patroni-managed primary must be reachable at the unit address."""
    output = await run_command_on_unit(
        ops_test,
        f"{APP_NAME}/0",
        "pg_isready -h $(hostname -i | awk '{print $1}') -p 5432",
    )
    assert "accepting connections" in output, (
        "the primary is not accepting connections at its own address"
    )
