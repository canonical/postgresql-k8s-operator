#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
import os
import uuid

import boto3
import pytest as pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_attempt, wait_exponential

from . import architecture
from .helpers import (
    DATABASE_APP_NAME,
    build_and_deploy,
    construct_endpoint,
    db_connect,
    get_password,
    get_primary,
    get_unit_address,
    scale_application,
)
from .juju_ import juju_major_version

CANNOT_RESTORE_PITR = "cannot restore PITR, juju debug-log for details"
S3_INTEGRATOR_APP_NAME = "s3-integrator"
if juju_major_version < 3:
    tls_certificates_app_name = "tls-certificates-operator"
    tls_channel = "legacy/edge" if architecture.architecture == "arm64" else "legacy/stable"
    tls_config = {"generate-self-signed-certificates": "true", "ca-common-name": "Test CA"}
else:
    tls_certificates_app_name = "self-signed-certificates"
    tls_channel = "latest/edge" if architecture.architecture == "arm64" else "latest/stable"
    tls_config = {"ca-common-name": "Test CA"}

logger = logging.getLogger(__name__)

AWS = "AWS"
GCP = "GCP"


@pytest.fixture(scope="module")
async def cloud_configs(ops_test: OpsTest) -> None:
    # Define some configurations and credentials.
    configs = {
        AWS: {
            "endpoint": "https://s3.amazonaws.com",
            "bucket": "data-charms-testing",
            "path": f"/postgresql-k8s/{uuid.uuid1()}",
            "region": "us-east-1",
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
            "access-key": os.environ["AWS_ACCESS_KEY"],
            "secret-key": os.environ["AWS_SECRET_KEY"],
        },
        GCP: {
            "access-key": os.environ["GCP_ACCESS_KEY"],
            "secret-key": os.environ["GCP_SECRET_KEY"],
        },
    }
    yield configs, credentials
    # Delete the previously created objects.
    logger.info("deleting the previously created backups")
    for cloud, config in configs.items():
        session = boto3.session.Session(
            aws_access_key_id=credentials[cloud]["access-key"],
            aws_secret_access_key=credentials[cloud]["secret-key"],
            region_name=config["region"],
        )
        s3 = session.resource(
            "s3", endpoint_url=construct_endpoint(config["endpoint"], config["region"])
        )
        bucket = s3.Bucket(config["bucket"])
        # GCS doesn't support batch delete operation, so delete the objects one by one.
        for bucket_object in bucket.objects.filter(Prefix=config["path"].lstrip("/")):
            bucket_object.delete()


async def pitr_backup_operations(
    ops_test: OpsTest,
    charm,
    s3_integrator_app_name: str,
    tls_certificates_app_name: str,
    tls_config,
    tls_channel,
    credentials,
    cloud,
    config,
) -> None:
    """Utility function containing PITR backup and timelines management operations for both cloud tests.

    Below is presented algorithm in the next format: "(timeline): action_1 -> action_2".
    1: table -> backup_b1 -> test_data_td1 -> timestamp_ts1 -> test_data_td2 -> restore_ts1 => 2
    2: check_td1 -> check_not_td2 -> test_data_td3 -> restore_b1_latest => 3
    3: check_td1 -> check_td2 -> check_not_td3 -> test_data_td4 -> restore_t2_latest => 4
    4: check_td1 -> check_not_td2 -> check_td3 -> check_not_td4
    """
    # Set-up environment
    database_app_name = f"{DATABASE_APP_NAME}-{cloud}"

    logger.info("deploying the next charms: s3-integrator, self-signed-certificates, postgresql")
    await ops_test.model.deploy(s3_integrator_app_name)
    await ops_test.model.deploy(tls_certificates_app_name, config=tls_config, channel=tls_channel)
    await build_and_deploy(
        ops_test, charm, 2, database_app_name=database_app_name, wait_for_idle=False
    )

    logger.info(
        "integrating self-signed-certificates with postgresql and waiting them to stabilize"
    )
    await ops_test.model.relate(database_app_name, tls_certificates_app_name)
    async with ops_test.fast_forward(fast_interval="60s"):
        await ops_test.model.wait_for_idle(
            apps=[database_app_name, tls_certificates_app_name],
            status="active",
            timeout=1000,
            raise_on_error=False,
        )

    # Configure and set access and secret keys.
    logger.info(f"configuring S3 integrator for {cloud}")
    await ops_test.model.applications[s3_integrator_app_name].set_config(config)
    action = await ops_test.model.units.get(f"{s3_integrator_app_name}/0").run_action(
        "sync-s3-credentials",
        **credentials,
    )
    await action.wait()

    logger.info("integrating s3-integrator with postgresql and waiting model to stabilize")
    await ops_test.model.relate(database_app_name, s3_integrator_app_name)
    async with ops_test.fast_forward(fast_interval="60s"):
        await ops_test.model.wait_for_idle(status="active", timeout=1000)

    primary = await get_primary(ops_test, database_app_name)
    for unit in ops_test.model.applications[database_app_name].units:
        if unit.name != primary:
            replica = unit.name
            break
    password = await get_password(ops_test, database_app_name=database_app_name)
    address = await get_unit_address(ops_test, primary)

    logger.info("1: creating table")
    _create_table(address, password)

    logger.info("1: creating backup b1")
    action = await ops_test.model.units.get(replica).run_action("create-backup")
    await action.wait()
    backup_status = action.results.get("backup-status")
    assert backup_status, "backup hasn't succeeded"
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(status="active", timeout=1000)
    backup_b1 = await _get_most_recent_backup(ops_test, ops_test.model.units.get(replica))

    logger.info("1: creating test data td1")
    _insert_test_data("test_data_td1", address, password)

    logger.info("1: get timestamp ts1")
    with db_connect(host=address, password=password) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT current_timestamp;")
        timestamp_ts1 = str(cursor.fetchone()[0])
    connection.close()
    # Wrong timestamp pointing to one year ahead
    unreachable_timestamp_ts1 = timestamp_ts1.replace(
        timestamp_ts1[:4], str(int(timestamp_ts1[:4]) + 1), 1
    )

    logger.info("1: creating test data td2")
    _insert_test_data("test_data_td2", address, password)

    logger.info("1: switching wal")
    _switch_wal(address, password)

    logger.info("1: scaling down to do restore")
    async with ops_test.fast_forward(fast_interval="60s"):
        await scale_application(ops_test, database_app_name, 1)
    remaining_unit = ops_test.model.units.get(f"{database_app_name}/0")

    logger.info("1: restoring the backup b1 with bad restore-to-time parameter")
    action = await ops_test.model.units.get(f"{database_app_name}/0").run_action(
        "restore", **{"backup-id": backup_b1, "restore-to-time": "bad data"}
    )
    await action.wait()
    assert action.status == "failed", (
        "1: restore must fail with bad restore-to-time parameter, but that action succeeded"
    )

    logger.info("1: restoring the backup b1 with unreachable restore-to-time parameter")
    action = await ops_test.model.units.get(f"{database_app_name}/0").run_action(
        "restore", **{"backup-id": backup_b1, "restore-to-time": unreachable_timestamp_ts1}
    )
    await action.wait()
    logger.info("1: waiting for the database charm to become blocked after restore")
    async with ops_test.fast_forward():
        await ops_test.model.block_until(
            lambda: ops_test.model.units.get(f"{database_app_name}/0").workload_status_message
            == CANNOT_RESTORE_PITR,
            timeout=1000,
        )
    logger.info(
        "1: database charm become in blocked state after restore, as supposed to be with unreachable PITR parameter"
    )

    for attempt in Retrying(
        stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
    ):
        with attempt:
            logger.info("1: restoring to the timestamp ts1")
            action = await remaining_unit.run_action(
                "restore", **{"restore-to-time": timestamp_ts1}
            )
            await action.wait()
            restore_status = action.results.get("restore-status")
            assert restore_status, "1: restore to the timestamp ts1 hasn't succeeded"
    await ops_test.model.wait_for_idle(status="active", timeout=1000, idle_period=30)

    logger.info("2: successful restore")
    primary = await get_primary(ops_test, database_app_name)
    address = await get_unit_address(ops_test, primary)
    timeline_t2 = await _get_most_recent_backup(ops_test, remaining_unit)
    assert backup_b1 != timeline_t2, "2: timeline 2 do not exist in list-backups action or bad"

    logger.info("2: checking test data td1")
    assert _check_test_data("test_data_td1", address, password), "2: test data td1 should exist"

    logger.info("2: checking not test data td2")
    assert not _check_test_data("test_data_td2", address, password), (
        "2: test data td2 shouldn't exist"
    )

    logger.info("2: creating test data td3")
    _insert_test_data("test_data_td3", address, password)

    logger.info("2: get timestamp ts2")
    with db_connect(host=address, password=password) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT current_timestamp;")
        timestamp_ts2 = str(cursor.fetchone()[0])
    connection.close()

    logger.info("2: creating test data td4")
    _insert_test_data("test_data_td4", address, password)

    logger.info("2: switching wal")
    _switch_wal(address, password)

    for attempt in Retrying(
        stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
    ):
        with attempt:
            logger.info("2: restoring the backup b1 to the latest")
            action = await remaining_unit.run_action(
                "restore", **{"backup-id": backup_b1, "restore-to-time": "latest"}
            )
            await action.wait()
            restore_status = action.results.get("restore-status")
            assert restore_status, "2: restore the backup b1 to the latest hasn't succeeded"
    await ops_test.model.wait_for_idle(status="active", timeout=1000, idle_period=30)

    logger.info("3: successful restore")
    primary = await get_primary(ops_test, database_app_name)
    address = await get_unit_address(ops_test, primary)
    timeline_t3 = await _get_most_recent_backup(ops_test, remaining_unit)
    assert backup_b1 != timeline_t3 and timeline_t2 != timeline_t3, (
        "3: timeline 3 do not exist in list-backups action or bad"
    )

    logger.info("3: checking test data td1")
    assert _check_test_data("test_data_td1", address, password), "3: test data td1 should exist"

    logger.info("3: checking test data td2")
    assert _check_test_data("test_data_td2", address, password), "3: test data td2 should exist"

    logger.info("3: checking not test data td3")
    assert not _check_test_data("test_data_td3", address, password), (
        "3: test data td3 shouldn't exist"
    )

    logger.info("3: checking not test data td4")
    assert not _check_test_data("test_data_td4", address, password), (
        "3: test data td4 shouldn't exist"
    )

    logger.info("3: switching wal")
    _switch_wal(address, password)

    for attempt in Retrying(
        stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
    ):
        with attempt:
            logger.info("3: restoring the timeline 2 to the latest")
            action = await remaining_unit.run_action(
                "restore", **{"backup-id": timeline_t2, "restore-to-time": "latest"}
            )
            await action.wait()
            restore_status = action.results.get("restore-status")
            assert restore_status, "3: restore the timeline 2 to the latest hasn't succeeded"
    await ops_test.model.wait_for_idle(status="active", timeout=1000, idle_period=30)

    logger.info("4: successful restore")
    primary = await get_primary(ops_test, database_app_name)
    address = await get_unit_address(ops_test, primary)
    timeline_t4 = await _get_most_recent_backup(ops_test, remaining_unit)
    assert (
        backup_b1 != timeline_t4 and timeline_t2 != timeline_t4 and timeline_t3 != timeline_t4
    ), "4: timeline 4 do not exist in list-backups action or bad"

    logger.info("4: checking test data td1")
    assert _check_test_data("test_data_td1", address, password), "4: test data td1 should exist"

    logger.info("4: checking not test data td2")
    assert not _check_test_data("test_data_td2", address, password), (
        "4: test data td2 shouldn't exist"
    )

    logger.info("4: checking test data td3")
    assert _check_test_data("test_data_td3", address, password), "4: test data td3 should exist"

    logger.info("4: checking test data td4")
    assert _check_test_data("test_data_td4", address, password), "4: test data td4 should exist"

    logger.info("4: switching wal")
    _switch_wal(address, password)

    for attempt in Retrying(
        stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
    ):
        with attempt:
            logger.info("4: restoring to the timestamp ts2")
            action = await remaining_unit.run_action(
                "restore", **{"restore-to-time": timestamp_ts2}
            )
            await action.wait()
            restore_status = action.results.get("restore-status")
            assert restore_status, "4: restore to the timestamp ts2 hasn't succeeded"
    await ops_test.model.wait_for_idle(status="active", timeout=1000, idle_period=30)

    logger.info("5: successful restore")
    primary = await get_primary(ops_test, database_app_name)
    address = await get_unit_address(ops_test, primary)
    timeline_t5 = await _get_most_recent_backup(ops_test, remaining_unit)
    assert (
        backup_b1 != timeline_t5
        and timeline_t2 != timeline_t5
        and timeline_t3 != timeline_t5
        and timeline_t4 != timeline_t5
    ), "5: timeline 5 do not exist in list-backups action or bad"

    logger.info("5: checking test data td1")
    assert _check_test_data("test_data_td1", address, password), "5: test data td1 should exist"

    logger.info("5: checking not test data td2")
    assert not _check_test_data("test_data_td2", address, password), (
        "5: test data td2 shouldn't exist"
    )

    logger.info("5: checking test data td3")
    assert _check_test_data("test_data_td3", address, password), "5: test data td3 should exist"

    logger.info("5: checking not test data td4")
    assert not _check_test_data("test_data_td4", address, password), (
        "5: test data td4 shouldn't exist"
    )

    await ops_test.model.wait_for_idle(status="active", timeout=1000)

    # Remove the database app.
    await ops_test.model.remove_application(database_app_name)
    await ops_test.model.block_until(
        lambda: database_app_name not in ops_test.model.applications, timeout=1000
    )
    # Remove the TLS operator.
    await ops_test.model.remove_application(tls_certificates_app_name)
    await ops_test.model.block_until(
        lambda: tls_certificates_app_name not in ops_test.model.applications, timeout=1000
    )


@pytest.mark.abort_on_fail
async def test_pitr_backup_gcp(ops_test: OpsTest, charm, cloud_configs: tuple[dict, dict]) -> None:
    """Build and deploy two units of PostgreSQL in GCP and then test PITR backup and restore actions."""
    config = cloud_configs[0][GCP]
    credentials = cloud_configs[1][GCP]
    cloud = GCP.lower()

    await pitr_backup_operations(
        ops_test,
        charm,
        S3_INTEGRATOR_APP_NAME,
        tls_certificates_app_name,
        tls_config,
        tls_channel,
        credentials,
        cloud,
        config,
    )


def _create_table(host: str, password: str):
    with db_connect(host=host, password=password) as connection:
        connection.autocommit = True
        connection.cursor().execute("CREATE TABLE IF NOT EXISTS backup_table (test_column TEXT);")
    connection.close()


def _insert_test_data(td: str, host: str, password: str):
    with db_connect(host=host, password=password) as connection:
        connection.autocommit = True
        connection.cursor().execute(
            "INSERT INTO backup_table (test_column) VALUES (%s);",
            (td,),
        )
    connection.close()


def _check_test_data(td: str, host: str, password: str) -> bool:
    with db_connect(host=host, password=password) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM backup_table WHERE test_column = %s);",
            (td,),
        )
        res = cursor.fetchone()[0]
    connection.close()
    return res


def _switch_wal(host: str, password: str):
    with db_connect(host=host, password=password) as connection:
        connection.autocommit = True
        connection.cursor().execute("SELECT pg_switch_wal();")
    connection.close()


async def _get_most_recent_backup(ops_test: OpsTest, unit: any) -> str:
    logger.info("listing the available backups")
    action = await unit.run_action("list-backups")
    await action.wait()
    backups = action.results.get("backups")
    assert backups, "backups not outputted"
    await ops_test.model.wait_for_idle(status="active", timeout=1000)
    most_recent_backup = backups.split("\n")[-1]
    return most_recent_backup.split()[0]
