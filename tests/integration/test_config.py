#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest as pytest
from pytest_operator.plugin import OpsTest

from .helpers import (
    DATABASE_APP_NAME,
    build_and_deploy,
    get_leader_unit,
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
        {"synchronous_node_count": ["0", "1"]},  # config option is greater than 0
        {
            "synchronous_node_count": [test_string, "all"]
        },  # config option is one of `all`, `minority` or `majority`
        {"connection_authentication_timeout": ["0", "60"]},  # config option is from 1 and 600
        {"connection_statement_timeout": ["-1", "0"]},  # config option is from 0 to 2147483647
        {
            "durability_synchronous_commit": [test_string, "on"]
        },  # config option is one of `on`, `remote_apply` or `remote_write`
        {
            "instance_default_text_search_config": [test_string, "pg_catalog.simple"]
        },  # config option is validated against the db
        {
            "instance_max_locks_per_transaction": ["-1", "64"]
        },  # config option is between 64 and 2147483647
        {
            "instance_password_encryption": [test_string, "scram-sha-256"]
        },  # config option is one of `md5` or `scram-sha-256`
        {"logging_client_min_messages": [test_string, "notice"]},
        # config option is one of 'debug5', 'debug4', 'debug3', 'debug2', 'debug1', 'log', 'notice', 'warning' or 'error'.
        {
            "logging_log_min_duration_statement": ["-2", "-1"]
        },  # config option is between -1 and 2147483647
        {
            "logging_track_functions": [test_string, "none"]
        },  # config option is one of 'none', 'pl', 'all'.
        {
            "memory_maintenance_work_mem": ["1023", "65536"]
        },  # config option is between 1024 and 2147483647
        {"memory_max_prepared_transactions": ["-1", "0"]},  # config option is between 0 and 262143
        {"memory_shared_buffers": ["15", "1024"]},  # config option is greater or equal than 16
        {"memory_temp_buffers": ["99", "1024"]},  # config option is between 100 and 1073741823
        {"memory_work_mem": ["63", "4096"]},  # config option is between 64 and 2147483647
        {
            "optimizer_constraint_exclusion": [test_string, "partition"]
        },  # config option is one of `on`, `off` or `partition`
        {
            "optimizer_cpu_index_tuple_cost": ["-1", "0.005"]
        },  # config option is between 0 and 1.80E+308
        {
            "optimizer_cpu_operator_cost": ["-1", "0.0025"]
        },  # config option is between 0 and 1.80E+308
        {"optimizer_cpu_tuple_cost": ["-1", "0.01"]},  # config option is between 0 and 1.80E+308
        {"optimizer_cursor_tuple_fraction": ["-1", "0.1"]},  # config option is between 0 and 1
        {
            "optimizer_default_statistics_target": ["0", "100"]
        },  # config option is between 1 and 10000
        {"optimizer_from_collapse_limit": ["0", "8"]},  # config option is between 1 and 2147483647
        {"optimizer_geqo_effort": ["-1", "5"]},  # config option is between 1 and 10
        {"optimizer_geqo_generations": ["-1", "0"]},  # config option is between 1 and 2147483647
        {"optimizer_geqo_pool_size": ["-1", "0"]},  # config option is between 1 and 2147483647
        {"optimizer_geqo_seed": ["-1", "0.0"]},  # config option is between 1 and 1
        {"optimizer_geqo_selection_bias": ["-1", "2.0"]},  # config option is between 1 and 2
        {"optimizer_geqo_threshold": ["-1", "12"]},  # config option is between 1 and 2147483647
        {
            "optimizer_jit_above_cost": ["-2", "100000.0"]
        },  # config option is between -1 and 1.80E+308
        {
            "optimizer_jit_inline_above_cost": ["-2", "500000.0"]
        },  # config option is between -1 and 1.80E+308
        {
            "optimizer_jit_optimize_above_cost": ["-2", "500000.0"]
        },  # config option is between -1 and 1.80E+308
        {"optimizer_join_collapse_limit": ["0", "8"]},  # config option is between 1 and 2147483647
        {
            "optimizer_min_parallel_index_scan_size": ["-1", "64"]
        },  # config option is between 0 and 715827882
        {
            "optimizer_min_parallel_table_scan_size": ["-1", "1024"]
        },  # config option is between 0 and 715827882
        {
            "optimizer_parallel_setup_cost": ["-1", "1000.0"]
        },  # config option is between 0 and 1.80E+308
        {
            "optimizer_parallel_tuple_cost": ["-1", "0.1"]
        },  # config option is between 0 and 1.80E+308
        {"profile": [test_string, "testing"]},  # config option is one of `testing` or `production`
        # {
        #    "profile_limit_memory": {"127", "128"}
        # },  # config option is between 128 and 9999999
        {
            "request_backslash_quote": [test_string, "safe_encoding"]
        },  # config option is one of `safe_encoding` and `on` and `off`
        {
            "request_date_style": [test_string, "ISO, MDY"]
        },  # config option is validated against the db
        {"request_deadlock_timeout": ["-1", "1000"]},  # config option is between 1 and 2147483647
        {
            "request_default_transaction_isolation": [test_string, "read committed"]
        },  # config option is one of `serializable`, `repeatable read`, `read committed`, `read uncommitted`.
        {"request_lock_timeout": ["-1", "0"]},  # config option is between 0 and 2147483647
        {"request_time_zone": [test_string, "UTC"]},  # config option is validated against the db
        {
            "request_track_activity_query_size": ["-1", "1024"]
        },  # config option is between 100 and 1048576
        {"request_xmlbinary": [test_string, "base64"]},  # config option is one of `base64`, `hex`.
        {
            "request_xmloption": [test_string, "content"]
        },  # config option is one of `content`, `document`.
        {
            "response_bytea_output": [test_string, "hex"]
        },  # config option is one of `escape` or `hex`
        {"response_extra_float_digits": ["5", "1"]},  # config option is between -15 and 3
        {
            "response_gin_fuzzy_search_limit": ["-1", "0"]
        },  # config option is between 0 and 2147483647
        {
            "response_lc_monetary": [test_string, "C"]
        },  # allowed values are the locales available in the unit.
        {
            "response_lc_numeric": [test_string, "C"]
        },  # allowed values are the locales available in the unit.
        {
            "response_lc_time": [test_string, "C"]
        },  # allowed values are the locales available in the unit.
        {
            "session_idle_in_transaction_session_timeout": ["-1", "0"]
        },  # config option is between 0 and 2147483647
        {
            "storage_bgwriter_lru_maxpages": ["-1", "100"]
        },  # config option is between 0 and 1073741823
        {"storage_bgwriter_lru_multiplier": ["-1", "2.0"]},  # config option is between 0 and 10
        {
            "storage_default_table_access_method": [test_string, "heap"]
        },  # config option entries can be created using the CREATE ACCESS METHOD SQL command. default `heap`
        {
            "storage_gin_pending_list_limit": ["-1", "4096"]
        },  # config option is between 64 and 2147483647
        {"storage_old_snapshot_threshold": ["-2", "-1"]},  # config option is between -1 and 86400
        {
            "vacuum_autovacuum_analyze_scale_factor": ["-1", "0.1"]
        },  # config option is between 0 and 100
        {
            "vacuum_autovacuum_analyze_threshold": ["-1", "50"]
        },  # config option is between 0 and 2147483647
        {
            "vacuum_autovacuum_freeze_max_age": ["99999", "200000000"]
        },  # config option is between 100000 and 2000000000
        {"vacuum_autovacuum_naptime": ["-1", "60"]},  # config option is between 1 and 2147483
        {
            "vacuum_autovacuum_vacuum_cost_delay": ["-2", "2.0"]
        },  # config option is between -1 and 100
        {
            "vacuum_autovacuum_vacuum_cost_limit": ["-2", "-1"]
        },  # config option is between -1 and 10000
        {
            "vacuum_autovacuum_vacuum_insert_scale_factor": ["-1", "0.2"]
        },  # config option is between 0 and 100
        {
            "vacuum_autovacuum_vacuum_insert_threshold": ["-2", "1000"]
        },  # config option is between -1 and 2147483647
        {
            "vacuum_autovacuum_vacuum_scale_factor": ["-1", "0.2"]
        },  # config option is between 0 and 100
        {
            "vacuum_autovacuum_vacuum_threshold": ["-1", "50"]
        },  # config option is between 0 and 2147483647
        {"vacuum_vacuum_cost_delay": ["-1", "0.0"]},  # config option is between 0 and 100
        {"vacuum_vacuum_cost_limit": ["-1", "200"]},  # config option is between 1 and 10000
        {"vacuum_vacuum_cost_page_dirty": ["-1", "20"]},  # config option is between 0 and 10000
        {"vacuum_vacuum_cost_page_hit": ["-1", "1"]},  # config option is between 0 and 10000
        {"vacuum_vacuum_cost_page_miss": ["-1", "2"]},  # config option is between 0 and 10000
        {
            "vacuum_vacuum_failsafe_age": ["-1", "1600000000"]
        },  # config option is between 0 and 2100000000
        {
            "vacuum_vacuum_freeze_min_age": ["-1", "50000000"]
        },  # config option is between 0 and 1000000000
        {
            "vacuum_vacuum_freeze_table_age": ["-1", "150000000"]
        },  # config option is between 0 and 2000000000
        {
            "vacuum_vacuum_multixact_failsafe_age": ["-1", "1600000000"]
        },  # config option is between 0 and 2100000000
        {
            "vacuum_vacuum_multixact_freeze_min_age": ["-1", "5000000"]
        },  # config option is between 0 and 1000000000
        {
            "vacuum_vacuum_multixact_freeze_table_age": ["-1", "150000000"]
        },  # config option is between 0 and 2000000000
    ]

    charm_config = {}
    for config in configs:
        for k, v in config.items():
            logger.info(k)
            charm_config[k] = v[0]
            await ops_test.model.applications[DATABASE_APP_NAME].set_config(charm_config)
            await ops_test.model.block_until(
                lambda: ops_test.model.units[f"{DATABASE_APP_NAME}/0"].workload_status
                == "blocked",
                timeout=100,
            )
            assert "Configuration Error" in leader_unit.workload_status_message
            charm_config[k] = v[1]

    await ops_test.model.applications[DATABASE_APP_NAME].set_config(charm_config)
    await ops_test.model.block_until(
        lambda: ops_test.model.units[f"{DATABASE_APP_NAME}/0"].workload_status == "active",
        timeout=100,
    )
