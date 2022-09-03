# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

from pathlib import Path
from typing import Optional

import psycopg2
import requests
import yaml
from pytest_operator.plugin import OpsTest
from tenacity import RetryError, Retrying, stop_after_delay, wait_fixed

from tests.integration.helpers import get_unit_address

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
PORT = 5432
APP_NAME = METADATA["name"]


class ProcessError(Exception):
    pass


async def app_name(ops_test: OpsTest) -> Optional[str]:
    """Returns the name of the cluster running PostgreSQL.

    This is important since not all deployments of the PostgreSQL charm have the application name
    "postgresql".

    Note: if multiple clusters are running PostgreSQL this will return the one first found.
    """
    status = await ops_test.model.get_status()
    for app in ops_test.model.applications:
        # note that format of the charm field is not exactly "postgresql"
        # \but instead takes the form of `local:focal/postgresql-6`
        if "postgresql" in status["applications"][app]["charm"]:
            return app

    return None


async def change_master_start_timeout(ops_test: OpsTest, seconds: int) -> None:
    """Change master start timeout configuration.

    Args:
        ops_test: ops_test instance.
        seconds: number of seconds to set in master_start_timeout configuration.
    """
    app = await app_name(ops_test)
    primary_name = await get_primary(ops_test, app)
    unit_ip = await get_unit_address(ops_test, primary_name)
    requests.patch(
        f"http://{unit_ip}:8008/config",
        json={"master_start_timeout": seconds},
    )


async def count_writes(ops_test: OpsTest) -> int:
    """New versions of pymongo no longer support the count operation, instead find is used."""
    app = await app_name(ops_test)
    password = await get_password(ops_test, app)
    # TODO: randomize or do something similar here to check multiple units.
    # hosts = [unit.public_address for unit in ops_test.model.applications[app].units]
    status = await ops_test.model.get_status()
    host = list(status["applications"][APP_NAME]["units"].values())[0]["address"]
    connection_string = (
        f"dbname='application' user='operator'"
        f" host='{host}' password='{password}' connect_timeout=10"
    )
    try:
        for attempt in Retrying(stop=stop_after_delay(30 * 2), wait=wait_fixed(3)):
            with attempt:
                with psycopg2.connect(
                    connection_string
                ) as connection, connection.cursor() as cursor:
                    cursor.execute("SELECT COUNT(number) FROM continuous_writes;")
                    count = cursor.fetchone()[0]
                connection.close()
    except RetryError:
        return -1
    return count


async def get_password(ops_test: OpsTest, app) -> str:
    """Use the charm action to retrieve the password from provided application.

    Returns:
        string with the password stored on the peer relation databag.
    """
    # Can retrieve from any unit running unit, so we pick the first.
    unit_name = ops_test.model.applications[app].units[0].name
    action = await ops_test.model.units.get(unit_name).run_action("get-password")
    action = await action.wait()
    return action.results["operator-password"]


async def get_primary(ops_test: OpsTest, app) -> str:
    """Use the charm action to retrieve the primary from provided application.

    Returns:
        string with the password stored on the peer relation databag.
    """
    # Can retrieve from any unit running unit, so we pick the first.
    unit_name = ops_test.model.applications[app].units[0].name
    action = await ops_test.model.units.get(unit_name).run_action("get-primary")
    action = await action.wait()
    return action.results["primary"]


async def kill_process(ops_test: OpsTest, unit_name: str, process: str, kill_code: str) -> None:
    """Kills process on the unit according to the provided kill code."""
    # killing the only replica can be disastrous
    app = await app_name(ops_test)
    if len(ops_test.model.applications[app].units) < 2:
        await ops_test.model.applications[app].add_unit(count=1)
        await ops_test.model.wait_for_idle(apps=[app], status="active", timeout=1000)

    kill_cmd = f"ssh --container postgresql {unit_name} pkill --signal {kill_code} {process}"
    return_code, _, _ = await ops_test.juju(*kill_cmd.split())

    if return_code != 0:
        raise ProcessError(
            "Expected kill command %s to succeed instead it failed: %s", kill_cmd, return_code
        )


async def postgresql_ready(ops_test, unit_name: str) -> bool:
    """Verifies a PostgreSQL instance is running and available."""
    unit_ip = await get_unit_address(ops_test, unit_name)
    try:
        for attempt in Retrying(stop=stop_after_delay(30 * 2), wait=wait_fixed(3)):
            with attempt:
                instance_health_info = requests.get(f"http://{unit_ip}:8008/health")
                assert instance_health_info.status_code == 200
    except RetryError:
        return False

    return True


async def secondary_up_to_date(ops_test: OpsTest, unit_ip, expected_writes) -> bool:
    """Checks if secondary is up to date with the cluster.

    Retries over the period of one minute to give secondary adequate time to copy over data.
    """
    app = await app_name(ops_test)
    password = await get_password(ops_test, app)
    # TODO: randomize or do something similar here to check multiple units.
    # hosts = [unit.public_address for unit in ops_test.model.applications[app].units]
    status = await ops_test.model.get_status()
    host = list(status["applications"][APP_NAME]["units"].values())[0]["address"]
    connection_string = (
        f"dbname='application' user='operator'"
        f" host='{host}' password='{password}' connect_timeout=10"
    )

    try:
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
            with attempt:
                with psycopg2.connect(
                    connection_string
                ) as connection, connection.cursor() as cursor:
                    cursor.execute("SELECT COUNT(number) FROM continuous_writes;")
                    secondary_writes = cursor.fetchone()[0]
                    print(secondary_writes)
                    print(expected_writes)
                    assert secondary_writes == expected_writes
    except RetryError:
        return False
    finally:
        connection.close()

    return True


async def stop_continuous_writes(ops_test: OpsTest) -> int:
    """Stops continuous writes to PostgreSQL and returns the last written value."""
    # stop the process
    action = await ops_test.model.units.get("application/0").run_action("stop-continuous-writes")
    action = await action.wait()
    return int(action.results["writes"])
