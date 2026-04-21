# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
"""Integration test: replica reinit after pg_control corruption via patronictl reinit."""

import logging
import time

import jubilant
import requests
from jubilant import Juju
from tenacity import Retrying, stop_after_delay, wait_fixed

from ..helpers import (
    ACTUAL_PGDATA_PATH,
    APPLICATION_NAME,
    DATABASE_APP_NAME,
    METADATA,
    STORAGE_PATH,
)
from .high_availability_helpers_new import (
    MINUTE_SECS,
    check_db_units_writes_increment,
    get_app_units,
    get_db_primary_unit,
    get_unit_ip,
    wait_for_apps_status,
)

logging.getLogger("jubilant.wait").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Path to the pg_control file inside the workload container.
PG_CONTROL_PATH = f"{ACTUAL_PGDATA_PATH}/global/pg_control"


def test_deploy(juju: Juju, charm: str) -> None:
    """Deploy a 3-unit PostgreSQL cluster and the continuous-writes test application."""
    resources = {"postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"]}

    logger.info("Deploying PostgreSQL cluster (%s, 3 units)", DATABASE_APP_NAME)
    juju.deploy(
        charm=charm,
        app=DATABASE_APP_NAME,
        base="ubuntu@24.04",
        config={"profile": "testing"},
        resources=resources,
        num_units=3,
        trust=True,
    )

    logger.info("Deploying test application (%s)", APPLICATION_NAME)
    juju.deploy(
        charm=APPLICATION_NAME,
        app=APPLICATION_NAME,
        base="ubuntu@24.04",
        channel="latest/edge",
        num_units=1,
    )

    juju.integrate(f"{DATABASE_APP_NAME}:database", f"{APPLICATION_NAME}:database")

    logger.info("Waiting for all applications to become active")
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, DATABASE_APP_NAME, APPLICATION_NAME),
        timeout=20 * MINUTE_SECS,
    )


def test_patroni_reinit_after_pg_control_deletion(juju: Juju, continuous_writes) -> None:
    """Verify a replica reinitialises correctly after pg_control deletion and patronictl reinit.

    Steps:
    1. Confirm the cluster is healthy and continuous writes are flowing.
    2. Stop Patroni on a replica, delete pg_control, then restart Patroni so that
       PostgreSQL fails to start due to the missing control file.
    3. Verify pebble logs on the replica record a pg_control startup failure.
    4. Run ``patronictl reinit`` inside the replica container to restore it from the primary.
    5. Confirm the replica recovers, that pg_control is present in the data directory,
       and that data integrity is maintained across all units.
    """
    logger.info("Identifying primary and replica units")
    primary_unit = get_db_primary_unit(juju, DATABASE_APP_NAME)
    all_units = get_app_units(juju, DATABASE_APP_NAME)
    replica_unit = next(unit for unit in all_units if unit != primary_unit)

    logger.info("Primary: %s | Replica under test: %s", primary_unit, replica_unit)

    primary_ip = get_unit_ip(juju, DATABASE_APP_NAME, primary_unit)
    replica_ip = get_unit_ip(juju, DATABASE_APP_NAME, replica_unit)

    # Patroni member names match Kubernetes pod names (e.g. "postgresql-k8s/1" -> "postgresql-k8s-1").
    replica_member_name = replica_unit.replace("/", "-")
    cluster_name = f"patroni-{DATABASE_APP_NAME}"

    # Kubernetes namespace equals the Juju model name.
    namespace = (juju.model or "").split(":")[-1]

    logger.info("Verifying initial health of replica %s", replica_unit)
    for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(5), reraise=True):
        with attempt:
            resp = requests.get(f"https://{replica_ip}:8008/health", verify=False)
            assert resp.status_code == 200, (
                f"Replica {replica_unit} unhealthy before test: HTTP {resp.status_code}"
            )

    logger.info("Verifying continuous writes are flowing")
    check_db_units_writes_increment(juju, DATABASE_APP_NAME)

    logger.info("Stopping Patroni on %s", replica_unit)
    juju.ssh(replica_unit, "/charm/bin/pebble stop postgresql", container="postgresql")

    logger.info("Deleting pg_control at %s on %s", PG_CONTROL_PATH, replica_unit)
    juju.ssh(replica_unit, f"rm {PG_CONTROL_PATH}", container="postgresql")

    logger.info("Starting Patroni on %s (pg_control is now missing)", replica_unit)
    juju.ssh(replica_unit, "/charm/bin/pebble start postgresql", container="postgresql")

    # Give pebble a moment to start the service and capture the first startup attempt in its logs.
    time.sleep(10)

    logger.info("Verifying pebble logs show a pg_control startup failure on %s", replica_unit)
    for attempt in Retrying(
        stop=stop_after_delay(2 * MINUTE_SECS), wait=wait_fixed(5), reraise=True
    ):
        with attempt:
            logs = juju.ssh(
                replica_unit,
                "/charm/bin/pebble logs postgresql -n all 2>&1 || true",
                container="postgresql",
            )
            assert "pg_control" in logs.lower(), (
                f"Expected pebble logs on {replica_unit} to contain a pg_control startup error; "
                f"last 1000 chars of logs: {logs[-1000:]!r}"
            )

    logger.info("Confirmed pg_control startup failure recorded in pebble logs on %s", replica_unit)

    # patronictl needs the same PATRONI_* environment variables that the Patroni pebble service
    # uses, but those are not available in a plain SSH session. We export them inline before the
    # command.
    logger.info("Running patronictl reinit on %s", replica_unit)
    k8s_labels = "{application: patroni, cluster-name: " + cluster_name + "}"
    patronictl_cmd = (
        f"PATRONI_SCOPE={cluster_name} "
        f"PATRONI_KUBERNETES_NAMESPACE={namespace} "
        f"PATRONI_KUBERNETES_USE_ENDPOINTS=true "
        f"PATRONI_KUBERNETES_LEADER_LABEL_VALUE=primary "
        f"PATRONI_KUBERNETES_LABELS='{k8s_labels}' "
        f"patronictl -c {STORAGE_PATH}/patroni.yml "
        f"reinit {cluster_name} {replica_member_name} --force"
    )
    juju.ssh(replica_unit, patronictl_cmd, container="postgresql")

    logger.info("Waiting for %s to recover after patronictl reinit", replica_unit)
    for attempt in Retrying(
        stop=stop_after_delay(5 * MINUTE_SECS), wait=wait_fixed(10), reraise=True
    ):
        with attempt:
            resp = requests.get(f"https://{replica_ip}:8008/health", verify=False)
            assert resp.status_code == 200, (
                f"Replica {replica_unit} still unhealthy after reinit: HTTP {resp.status_code}"
            )

    for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(5), reraise=True):
        with attempt:
            cluster_resp = requests.get(f"https://{primary_ip}:8008/cluster", verify=False)
            members = cluster_resp.json()["members"]
            replica_state = next(
                (m["state"] for m in members if m["name"] == replica_member_name), None
            )
            assert replica_state in ("running", "streaming"), (
                f"Replica {replica_unit} not streaming after reinit: {replica_state!r}"
            )

    logger.info("Replica %s is healthy and streaming after reinit", replica_unit)

    logger.info("Verifying pg_control exists in data directory of %s after reinit", replica_unit)
    result = juju.ssh(
        replica_unit,
        f"test -f {PG_CONTROL_PATH} && echo exists || echo missing",
        container="postgresql",
    )
    assert "exists" in result, (
        f"pg_control is missing from {replica_unit} after reinit (expected at {PG_CONTROL_PATH})"
    )
    logger.info("pg_control is present at %s on %s", PG_CONTROL_PATH, replica_unit)

    logger.info("Verifying data integrity on all units after reinit")
    check_db_units_writes_increment(juju, DATABASE_APP_NAME)
    logger.info(
        "Data integrity verified: all units (including %s) have consistent data", replica_unit
    )
