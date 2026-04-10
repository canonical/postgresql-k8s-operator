#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from pathlib import Path

import pytest
import requests
import yaml
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from .helpers import DATABASE_APP_NAME, get_primary, get_unit_address

logger = logging.getLogger(__name__)

MINUTE = 60
TIMEOUT = 20 * MINUTE
REINIT_TIMEOUT = 20 * MINUTE


async def _run_command_on_unit(ops_test: OpsTest, unit_name: str, command: str) -> str:
    """Run a command on a unit and return stdout."""
    complete_command = ["ssh", "--container", "postgresql", unit_name, command]
    returncode, stdout, stderr = await ops_test.juju(*complete_command)
    if returncode != 0:
        raise RuntimeError(
            f"Command failed ({returncode}): {command}\nstdout:\n{stdout}\nstderr:\n{stderr}"
        )
    return stdout


async def _get_cluster_state(ops_test: OpsTest, unit_name: str) -> dict:
    """Get the Patroni cluster state via REST API."""
    unit_ip = await get_unit_address(ops_test, unit_name)
    # Using 'verify=False' because we use IP addresses which won't match the certificate
    response = requests.get(f"https://{unit_ip}:8008/cluster", verify=False)
    response.raise_for_status()
    return response.json()


def _member_role_state(cluster_state: dict, member_name: str) -> tuple[str | None, str | None]:
    """Parse cluster state JSON for a member's role and state."""
    for member in cluster_state.get("members", []):
        if member["name"] == member_name:
            return member["role"], member["state"].lower() if member["state"] else None
    return None, None


METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())


@pytest.mark.abort_on_fail
async def test_reinit_after_pg_control_removal(ops_test: OpsTest, charm) -> None:
    """Remove pg_control on a replica and verify `reinit` action recovers it."""
    if DATABASE_APP_NAME not in ops_test.model.applications:
        resources = {
            "postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"]
        }
        await ops_test.model.deploy(
            charm,
            resources=resources,
            application_name=DATABASE_APP_NAME,
            num_units=3,
            trust=True,
        )

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME], status="active", timeout=TIMEOUT
        )

    unit_names = [unit.name for unit in ops_test.model.applications[DATABASE_APP_NAME].units]
    assert len(unit_names) == 3, f"Expected 3 units, got {len(unit_names)}: {unit_names}"

    primary_unit = await get_primary(ops_test)
    replica_unit = next(unit for unit in unit_names if unit != primary_unit)
    replica_member = replica_unit.replace("/", "-")

    # Debug: List what's in /var/lib/pg
    stdout = await _run_command_on_unit(ops_test, replica_unit, "ls -laR /var/lib/pg")
    logger.info(f"Directory listing for /var/lib/pg:\n{stdout}")

    # Locate pg_control dynamically
    stdout = await _run_command_on_unit(
        ops_test, replica_unit, "find /var/lib/pg -name pg_control"
    )
    pg_control_files = stdout.strip().splitlines()
    assert pg_control_files, f"pg_control file not found in /var/lib/pg. ls output:\n{stdout}"
    pg_control_file = pg_control_files[0]
    logger.info("Found pg_control at: %s", pg_control_file)

    # Verify pg_control exists
    await _run_command_on_unit(ops_test, replica_unit, f"test -f {pg_control_file}")

    # Remove pg_control and restart Patroni
    await _run_command_on_unit(
        ops_test,
        replica_unit,
        f"rm -f {pg_control_file}",
    )
    # Restart Patroni (Pebble will restart it)
    logger.info("Restarting postgresql service to trigger recovery")
    await _run_command_on_unit(ops_test, replica_unit, "pebble restart postgresql")

    # Verify it crashes (optional but good for debugging)
    logger.info("Verifying service is failing after pg_control removal")
    for attempt in Retrying(stop=stop_after_delay(2 * MINUTE), wait=wait_fixed(10), reraise=True):
        with attempt:
            logs = await _run_command_on_unit(ops_test, replica_unit, "pebble logs postgresql")
            assert "pg_controldata: error: could not open file" in logs, (
                f"Expected crash log not found. Logs:\n{logs}"
            )

    # Run reinit action
    logger.info("Running reinit action on %s", replica_unit)
    action = await ops_test.model.units.get(replica_unit).run_action("reinit")
    await action.wait()
    assert action.status == "completed"
    assert "reinitialize" in action.results["result"]

    # Wait for recovery
    logger.info("Waiting for pg_control to be restored and service to be healthy")
    for attempt in Retrying(
        stop=stop_after_delay(REINIT_TIMEOUT),
        wait=wait_fixed(10),
        reraise=True,
    ):
        with attempt:
            # Check if pg_control is back
            await _run_command_on_unit(ops_test, replica_unit, f"test -f {pg_control_file}")

            cluster_state = await _get_cluster_state(ops_test, primary_unit)
            role, state = _member_role_state(cluster_state, replica_member)
            assert role in {"replica", "sync_standby", "standby_leader", "leader"}, (
                f"Unexpected role for {replica_member}: {role}\n{cluster_state}"
            )
            assert state in {"running", "streaming"}, (
                f"Unexpected state for {replica_member}: {state}\n{cluster_state}"
            )

    await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=TIMEOUT)
