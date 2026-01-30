#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest as pytest
from pytest_operator.plugin import OpsTest

from .helpers import (
    DATABASE_APP_NAME,
    build_and_deploy,
    execute_query_on_unit,
    get_leader_unit,
    get_password,
    get_unit_address,
)

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_config_parameters(ops_test: OpsTest, charm) -> None:
    """Build and deploy one unit of PostgreSQL and then test config with wrong parameters."""
    # Build and deploy the PostgreSQL charm.
    async with ops_test.fast_forward():
        await build_and_deploy(ops_test, charm, 1)

    await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active")

    leader_unit = await get_leader_unit(ops_test, DATABASE_APP_NAME)
    test_string = "abcXYZ123"

    configs = [
        {"synchronous-node-count": ["0", "1"]},  # config option is greater than 0
        {
            "synchronous-node-count": [test_string, "all"]
        },  # config option is one of `all`, `minority` or `majority`
        {"connection-authentication-timeout": ["0", "60"]},  # config option is from 1 and 600
        {"connection-statement-timeout": ["-1", "0"]},  # config option is from 0 to 2147483647
        {"durability-maximum-lag-on-failover": ["-1", "1024"]},  # config option is integer
        {
            "durability-synchronous-commit": [test_string, "on"]
        },  # config option is one of `on`, `remote_apply` or `remote_write`
        {
            "instance-default-text-search-config": [test_string, "pg_catalog.simple"]
        },  # config option is validated against the db
        {
            "instance-max-locks-per-transaction": ["-1", "64"]
        },  # config option is between 64 and 2147483647
        {
            "instance-password-encryption": [test_string, "scram-sha-256"]
        },  # config option is one of `md5` or `scram-sha-256`
        {
            "instance-password-encryption": [test_string, "md5"]
        },  # config option is one of `md5` or `scram-sha-256`
        {"logging-client-min-messages": [test_string, "notice"]},
        # config option is one of 'debug5', 'debug4', 'debug3', 'debug2', 'debug1', 'log', 'notice', 'warning' or 'error'.
        {
            "logging-log-min-duration-statement": ["-2", "-1"]
        },  # config option is between -1 and 2147483647
        {
            "logging-track-functions": [test_string, "none"]
        },  # config option is one of 'none', 'pl', 'all'.
        {
            "memory-maintenance-work-mem": ["1023", "65536"]
        },  # config option is between 1024 and 2147483647
        {"memory-max-prepared-transactions": ["-1", "0"]},  # config option is between 0 and 262143
        {"memory-shared-buffers": ["15", "1024"]},  # config option is greater or equal than 16
        {"memory-temp-buffers": ["99", "1024"]},  # config option is between 100 and 1073741823
        {"memory-work-mem": ["63", "4096"]},  # config option is between 64 and 2147483647
        {
            "optimizer-constraint-exclusion": [test_string, "partition"]
        },  # config option is one of `on`, `off` or `partition`
        {
            "optimizer-cpu-index-tuple-cost": ["-1", "0.005"]
        },  # config option is between 0 and 1.80E+308
        {
            "optimizer-cpu-operator-cost": ["-1", "0.0025"]
        },  # config option is between 0 and 1.80E+308
        {"optimizer-cpu-tuple-cost": ["-1", "0.01"]},  # config option is between 0 and 1.80E+308
        {"optimizer-cursor-tuple-fraction": ["-1", "0.1"]},  # config option is between 0 and 1
        {
            "optimizer-default-statistics-target": ["0", "100"]
        },  # config option is between 1 and 10000
        {"optimizer-from-collapse-limit": ["0", "8"]},  # config option is between 1 and 2147483647
        {"optimizer-geqo-effort": ["-1", "5"]},  # config option is between 1 and 10
        {"optimizer-geqo-generations": ["-1", "0"]},  # config option is between 1 and 2147483647
        {"optimizer-geqo-pool-size": ["-1", "0"]},  # config option is between 1 and 2147483647
        {"optimizer-geqo-seed": ["-1", "0.0"]},  # config option is between 1 and 1
        {"optimizer-geqo-selection-bias": ["-1", "2.0"]},  # config option is between 1 and 2
        {"optimizer-geqo-threshold": ["-1", "12"]},  # config option is between 1 and 2147483647
        {
            "optimizer-jit-above-cost": ["-2", "100000.0"]
        },  # config option is between -1 and 1.80E+308
        {
            "optimizer-jit-inline-above-cost": ["-2", "500000.0"]
        },  # config option is between -1 and 1.80E+308
        {
            "optimizer-jit-optimize-above-cost": ["-2", "500000.0"]
        },  # config option is between -1 and 1.80E+308
        {"optimizer-join-collapse-limit": ["0", "8"]},  # config option is between 1 and 2147483647
        {
            "optimizer-min-parallel-index-scan-size": ["-1", "64"]
        },  # config option is between 0 and 715827882
        {
            "optimizer-min-parallel-table-scan-size": ["-1", "1024"]
        },  # config option is between 0 and 715827882
        {
            "optimizer-parallel-setup-cost": ["-1", "1000.0"]
        },  # config option is between 0 and 1.80E+308
        {
            "optimizer-parallel-tuple-cost": ["-1", "0.1"]
        },  # config option is between 0 and 1.80E+308
        {"profile": [test_string, "testing"]},  # config option is one of `testing` or `production`
        {"profile-limit-memory": ["127", "128"]},  # config option is between 128 and 9999999
        {
            "request-backslash-quote": [test_string, "safe_encoding"]
        },  # config option is one of `safe_encoding` and `on` and `off`
        {
            "request-date-style": [test_string, "ISO, MDY"]
        },  # config option is validated against the db
        {"request-deadlock-timeout": ["-1", "1000"]},  # config option is between 1 and 2147483647
        {
            "request-default-transaction-isolation": [test_string, "read committed"]
        },  # config option is one of `serializable`, `repeatable read`, `read committed`, `read uncommitted`.
        {"request-lock-timeout": ["-1", "0"]},  # config option is between 0 and 2147483647
        {"request-time-zone": [test_string, "UTC"]},  # config option is validated against the db
        {
            "request-track-activity-query-size": ["-1", "1024"]
        },  # config option is between 100 and 1048576
        {"request-xmlbinary": [test_string, "base64"]},  # config option is one of `base64`, `hex`.
        {
            "request-xmloption": [test_string, "content"]
        },  # config option is one of `content`, `document`.
        {
            "response-bytea-output": [test_string, "hex"]
        },  # config option is one of `escape` or `hex`
        {"response-extra-float-digits": ["5", "1"]},  # config option is between -15 and 3
        {
            "response-gin-fuzzy-search-limit": ["-1", "0"]
        },  # config option is between 0 and 2147483647
        {
            "response-lc-monetary": [test_string, "C"]
        },  # allowed values are the locales available in the unit.
        {
            "response-lc-numeric": [test_string, "C"]
        },  # allowed values are the locales available in the unit.
        {
            "response-lc-time": [test_string, "C"]
        },  # allowed values are the locales available in the unit.
        {
            "session-idle-in-transaction-session-timeout": ["-1", "0"]
        },  # config option is between 0 and 2147483647
        {
            "storage-bgwriter-lru-maxpages": ["-1", "100"]
        },  # config option is between 0 and 1073741823
        {"storage-bgwriter-lru-multiplier": ["-1", "2.0"]},  # config option is between 0 and 10
        {
            "storage-default-table-access-method": [test_string, "heap"]
        },  # config option entries can be created using the CREATE ACCESS METHOD SQL command. default `heap`
        {
            "storage-gin-pending-list-limit": ["-1", "4096"]
        },  # config option is between 64 and 2147483647
        {"storage-old-snapshot-threshold": ["-2", "-1"]},  # config option is between -1 and 86400
        {
            "vacuum-autovacuum-analyze-scale-factor": ["-1", "0.1"]
        },  # config option is between 0 and 100
        {
            "vacuum-autovacuum-analyze-threshold": ["-1", "50"]
        },  # config option is between 0 and 2147483647
        {
            "vacuum-autovacuum-freeze-max-age": ["99999", "200000000"]
        },  # config option is between 100000 and 2000000000
        {"vacuum-autovacuum-naptime": ["-1", "60"]},  # config option is between 1 and 2147483
        {
            "vacuum-autovacuum-vacuum-cost-delay": ["-2", "2.0"]
        },  # config option is between -1 and 100
        {
            "vacuum-autovacuum-vacuum-cost-limit": ["-2", "-1"]
        },  # config option is between -1 and 10000
        {
            "vacuum-autovacuum-vacuum-insert-scale-factor": ["-1", "0.2"]
        },  # config option is between 0 and 100
        {
            "vacuum-autovacuum-vacuum-insert-threshold": ["-2", "1000"]
        },  # config option is between -1 and 2147483647
        {
            "vacuum-autovacuum-vacuum-scale-factor": ["-1", "0.2"]
        },  # config option is between 0 and 100
        {
            "vacuum-autovacuum-vacuum-threshold": ["-1", "50"]
        },  # config option is between 0 and 2147483647
        {"vacuum-vacuum-cost-delay": ["-1", "0.0"]},  # config option is between 0 and 100
        {"vacuum-vacuum-cost-limit": ["-1", "200"]},  # config option is between 1 and 10000
        {"vacuum-vacuum-cost-page-dirty": ["-1", "20"]},  # config option is between 0 and 10000
        {"vacuum-vacuum-cost-page-hit": ["-1", "1"]},  # config option is between 0 and 10000
        {"vacuum-vacuum-cost-page-miss": ["-1", "2"]},  # config option is between 0 and 10000
        {
            "vacuum-vacuum-failsafe-age": ["-1", "1600000000"]
        },  # config option is between 0 and 2100000000
        {
            "vacuum-vacuum-freeze-min-age": ["-1", "50000000"]
        },  # config option is between 0 and 1000000000
        {
            "vacuum-vacuum-freeze-table-age": ["-1", "150000000"]
        },  # config option is between 0 and 2000000000
        {
            "vacuum-vacuum-multixact-failsafe-age": ["-1", "1600000000"]
        },  # config option is between 0 and 2100000000
        {
            "vacuum-vacuum-multixact-freeze-min-age": ["-1", "5000000"]
        },  # config option is between 0 and 1000000000
        {
            "vacuum-vacuum-multixact-freeze-table-age": ["-1", "150000000"]
        },  # config option is between 0 and 2000000000
        # Worker process configs
        {"cpu-max-worker-processes": ["-1", "16"]},  # negative (invalid) and valid value
        {"cpu-max-worker-processes": ["1", "2"]},  # below min (1<2) and valid min value
        {
            "cpu-max-worker-processes": ["invalid", "auto"]
        },  # config option is "auto" or a positive integer
        {"cpu-max-parallel-workers": ["-1", "16"]},  # negative (invalid) and valid value
        {"cpu-max-parallel-workers": ["1", "2"]},  # below min (1<2) and valid min value
        {
            "cpu-max-parallel-workers": ["invalid", "auto"]
        },  # config option is "auto" or a positive integer
        {"cpu-max-parallel-maintenance-workers": ["-1", "0"]},  # negative and zero (both invalid)
        {
            "cpu-max-parallel-maintenance-workers": ["1", "100"]
        },  # below min (1<2) and above max (100>10*vCores)
        {
            "cpu-max-parallel-maintenance-workers": ["invalid", "auto"]
        },  # config option is "auto" or a positive integer
        {"cpu-max-logical-replication-workers": ["-1", "0"]},  # negative and zero (both invalid)
        {
            "cpu-max-logical-replication-workers": ["1", "100"]
        },  # below min (1<2) and above max (100>10*vCores)
        {
            "cpu-max-logical-replication-workers": ["invalid", "auto"]
        },  # config option is "auto" or a positive integer
        {"cpu-max-sync-workers-per-subscription": ["-1", "0"]},  # negative and zero (both invalid)
        {
            "cpu-max-sync-workers-per-subscription": ["1", "100"]
        },  # below min (1<2) and above max (100>10*vCores)
        {
            "cpu-max-sync-workers-per-subscription": ["invalid", "auto"]
        },  # config option is "auto" or a positive integer
        {
            "cpu-max-parallel-apply-workers-per-subscription": ["-1", "0"]
        },  # negative and zero (both invalid)
        {
            "cpu-max-parallel-apply-workers-per-subscription": ["1", "100"]
        },  # below min (1<2) and above max (100>10*vCores)
        {
            "cpu-max-parallel-apply-workers-per-subscription": ["invalid", "auto"]
        },  # config option is "auto" or a positive integer
    ]

    charm_config = {}
    for config in configs:
        for k, v in config.items():
            logger.info(k)
            charm_config[k] = v[0]
            await ops_test.model.applications[DATABASE_APP_NAME].set_config(charm_config)
            await ops_test.model.block_until(
                lambda: (
                    ops_test.model.units[f"{DATABASE_APP_NAME}/0"].workload_status == "blocked"
                ),
                timeout=100,
            )
            assert "Configuration Error" in leader_unit.workload_status_message
            charm_config[k] = v[1]

    await ops_test.model.applications[DATABASE_APP_NAME].set_config(charm_config)
    await ops_test.model.block_until(
        lambda: ops_test.model.units[f"{DATABASE_APP_NAME}/0"].workload_status == "active",
        timeout=100,
    )


@pytest.mark.abort_on_fail
async def test_worker_process_configs(ops_test: OpsTest) -> None:
    """Test worker process configuration parameters are applied correctly."""
    leader_unit = await get_leader_unit(ops_test, DATABASE_APP_NAME)
    leader_unit_name = leader_unit.name
    password = await get_password(ops_test)
    unit_address = await get_unit_address(ops_test, leader_unit_name)

    # Test setting explicit numeric values (all values must be >= 2 per validation)
    worker_configs = {
        "cpu-max-worker-processes": "16",
        "cpu-max-parallel-workers": "8",
        "cpu-max-parallel-maintenance-workers": "8",
        "cpu-max-logical-replication-workers": "8",
        "cpu-max-sync-workers-per-subscription": "8",
        "cpu-max-parallel-apply-workers-per-subscription": "8",
    }

    await ops_test.model.applications[DATABASE_APP_NAME].set_config(worker_configs)
    await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=300)

    # Verify the configs are applied in PostgreSQL
    # Map charm config names to PostgreSQL parameter names
    config_to_pg_param = {
        "cpu-max-worker-processes": "max_worker_processes",
        "cpu-max-parallel-workers": "max_parallel_workers",
        "cpu-max-parallel-maintenance-workers": "max_parallel_maintenance_workers",
        "cpu-max-logical-replication-workers": "max_logical_replication_workers",
        "cpu-max-sync-workers-per-subscription": "max_sync_workers_per_subscription",
        "cpu-max-parallel-apply-workers-per-subscription": "max_parallel_apply_workers_per_subscription",
    }

    for config_name, expected_value in worker_configs.items():
        pg_param = config_to_pg_param.get(config_name, config_name.replace("-", "_"))
        result = await execute_query_on_unit(unit_address, password, f"SHOW {pg_param}")
        actual_value = str(result[0]) if result else ""
        assert actual_value == expected_value, (
            f"{pg_param}: expected {expected_value}, got {actual_value}"
        )

    # Test setting "auto" values
    auto_configs = {
        "cpu-max-worker-processes": "auto",
        "cpu-max-parallel-workers": "auto",
        "cpu-max-parallel-maintenance-workers": "auto",
        "cpu-max-logical-replication-workers": "auto",
        "cpu-max-sync-workers-per-subscription": "auto",
        "cpu-max-parallel-apply-workers-per-subscription": "auto",
    }

    await ops_test.model.applications[DATABASE_APP_NAME].set_config(auto_configs)
    await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=300)

    # Verify "auto" values are resolved to integers (not the string "auto")
    for config_name in auto_configs:
        pg_param = config_to_pg_param.get(config_name, config_name.replace("-", "_"))
        result = await execute_query_on_unit(unit_address, password, f"SHOW {pg_param}")
        actual_value = str(result[0]) if result else ""
        assert actual_value != "auto", f"{pg_param} should be resolved to a number, not 'auto'"
        assert actual_value.isdigit(), f"{pg_param} should be a number, got '{actual_value}'"


@pytest.mark.abort_on_fail
async def test_wal_compression_config(ops_test: OpsTest) -> None:
    """Test wal_compression configuration parameter."""
    leader_unit = await get_leader_unit(ops_test, DATABASE_APP_NAME)
    leader_unit_name = leader_unit.name
    password = await get_password(ops_test)
    unit_address = await get_unit_address(ops_test, leader_unit_name)

    # Test enabling WAL compression
    await ops_test.model.applications[DATABASE_APP_NAME].set_config({
        "cpu-wal-compression": "true"
    })
    await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=300)

    result = await execute_query_on_unit(unit_address, password, "SHOW wal_compression")
    # Verify it's a known compression algorithm
    known_algorithms = ["pglz", "lz4", "zstd"]
    assert result[0] in known_algorithms, (
        f"Expected a known compression algorithm, got '{result[0]}'"
    )

    # Test disabling WAL compression
    await ops_test.model.applications[DATABASE_APP_NAME].set_config({
        "cpu-wal-compression": "false"
    })
    await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=300)

    result = await execute_query_on_unit(unit_address, password, "SHOW wal_compression")
    assert result[0] == "off"
