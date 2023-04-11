# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio
from pathlib import Path
from typing import Dict, Optional, Tuple

import psycopg2
import requests
import yaml
from kubernetes import config
from kubernetes.client.api import core_v1_api
from kubernetes.stream import stream
from lightkube.core.client import Client
from lightkube.resources.core_v1 import Pod
from pytest_operator.plugin import OpsTest
from tenacity import RetryError, Retrying, stop_after_delay, wait_fixed

from tests.integration.helpers import (
    app_name,
    get_password,
    get_primary,
    get_unit_address,
)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
PORT = 5432


class MemberNotListedOnClusterError(Exception):
    """Raised when a member is not listed in the cluster."""


class MemberNotUpdatedOnClusterError(Exception):
    """Raised when a member is not yet updated in the cluster."""


class ProcessError(Exception):
    """Raised when a process fails."""


def get_patroni_cluster(unit_ip: str) -> Dict[str, str]:
    resp = requests.get(f"http://{unit_ip}:8008/cluster")
    return resp.json()


async def change_primary_start_timeout(ops_test: OpsTest, seconds: Optional[int]) -> None:
    """Change primary start timeout configuration.

    Args:
        ops_test: ops_test instance.
        seconds: number of seconds to set in primary_start_timeout configuration.
    """
    for attempt in Retrying(stop=stop_after_delay(30 * 2), wait=wait_fixed(3)):
        with attempt:
            app = await app_name(ops_test)
            primary_name = await get_primary(ops_test, app)
            unit_ip = await get_unit_address(ops_test, primary_name)
            requests.patch(
                f"http://{unit_ip}:8008/config",
                json={"primary_start_timeout": seconds},
            )


async def check_writes(ops_test) -> int:
    """Gets the total writes from the test charm and compares to the writes from db."""
    total_expected_writes = await stop_continuous_writes(ops_test)
    actual_writes, max_number_written = await count_writes(ops_test)
    for member, count in actual_writes.items():
        assert (
            count == max_number_written[member]
        ), f"{member}: writes to the db were missed: count of actual writes different from the max number written."
        assert total_expected_writes == count, f"{member}: writes to the db were missed."
    return total_expected_writes


async def check_writes_are_increasing(ops_test, down_unit: str) -> None:
    """Verify new writes are continuing by counting the number of writes."""
    writes, _ = await count_writes(ops_test, down_unit=down_unit)
    for member, count in writes.items():
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
            with attempt:
                more_writes, _ = await count_writes(ops_test, down_unit=down_unit)
                assert more_writes[member] > count, f"{member}: writes not continuing to DB"


async def count_writes(
    ops_test: OpsTest, down_unit: str = None
) -> Tuple[Dict[str, int], Dict[str, int]]:
    """Count the number of writes in the database."""
    app = await app_name(ops_test)
    password = await get_password(ops_test, database_app_name=app, down_unit=down_unit)
    status = await ops_test.model.get_status()
    for unit_name, unit in status["applications"][app]["units"].items():
        if unit_name != down_unit:
            cluster = get_patroni_cluster(unit["address"])
            break
    count = {}
    max = {}
    for member in cluster["members"]:
        if member["role"] != "replica" and member["host"].split(".")[0] != (
            down_unit or ""
        ).replace("/", "-"):
            host = member["host"]

            # Translate the service hostname to an IP address.
            model = ops_test.model.info
            client = Client(namespace=model.name)
            service = client.get(Pod, name=host.split(".")[0])
            ip = service.status.podIP

            connection_string = (
                f"dbname='application' user='operator'"
                f" host='{ip}' password='{password}' connect_timeout=10"
            )

            with psycopg2.connect(connection_string) as connection, connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(number), MAX(number) FROM continuous_writes;")
                results = cursor.fetchone()
                count[member["name"]] = results[0]
                max[member["name"]] = results[1]
            connection.close()
    return count, max


async def fetch_cluster_members(ops_test: OpsTest):
    """Fetches the IPs listed by Patroni as cluster members.

    Args:
        ops_test: OpsTest instance.
    """

    def get_host_ip(host: str) -> str:
        # Translate the pod hostname to an IP address.
        model = ops_test.model.info
        client = Client(namespace=model.name)
        pod = client.get(Pod, name=host.split(".")[0])
        return pod.status.podIP

    app = await app_name(ops_test)
    member_ips = {}
    for unit in ops_test.model.applications[app].units:
        unit_address = await get_unit_address(ops_test, unit.name)
        cluster_info = requests.get(f"http://{unit_address}:8008/cluster")
        if len(member_ips) > 0:
            # If the list of members IPs was already fetched, also compare the
            # list provided by other members.
            assert member_ips == {
                get_host_ip(member["host"]) for member in cluster_info.json()["members"]
            }, "members report different lists of cluster members."
        else:
            member_ips = {get_host_ip(member["host"]) for member in cluster_info.json()["members"]}
    return member_ips


async def get_primary_start_timeout(ops_test: OpsTest) -> Optional[int]:
    """Get the primary start timeout configuration.

    Args:
        ops_test: ops_test instance.

    Returns:
        primary start timeout in seconds or None if it's using the default value.
    """
    for attempt in Retrying(stop=stop_after_delay(30 * 2), wait=wait_fixed(3)):
        with attempt:
            app = await app_name(ops_test)
            primary_name = await get_primary(ops_test, app)
            unit_ip = await get_unit_address(ops_test, primary_name)
            configuration_info = requests.get(f"http://{unit_ip}:8008/config")
            primary_start_timeout = configuration_info.json().get("primary_start_timeout")
            return int(primary_start_timeout) if primary_start_timeout is not None else None


async def is_replica(ops_test: OpsTest, unit_name: str) -> bool:
    """Returns whether the unit a replica in the cluster."""
    unit_ip = await get_unit_address(ops_test, unit_name)
    member_name = unit_name.replace("/", "-")

    try:
        for attempt in Retrying(stop=stop_after_delay(60 * 3), wait=wait_fixed(3)):
            with attempt:
                cluster_info = requests.get(f"http://{unit_ip}:8008/cluster")

                # The unit may take some time to be listed on Patroni REST API cluster endpoint.
                if member_name not in {
                    member["name"] for member in cluster_info.json()["members"]
                }:
                    raise MemberNotListedOnClusterError()

                for member in cluster_info.json()["members"]:
                    if member["name"] == member_name:
                        role = member["role"]

                # A member that restarted has the DB process stopped may
                # take some time to know that a new primary was elected.
                if role != "leader":
                    return True
                else:
                    raise MemberNotUpdatedOnClusterError()
    except RetryError:
        return False


async def postgresql_ready(ops_test, unit_name: str) -> bool:
    """Verifies a PostgreSQL instance is running and available."""
    unit_ip = await get_unit_address(ops_test, unit_name)
    try:
        for attempt in Retrying(stop=stop_after_delay(60 * 5), wait=wait_fixed(3)):
            with attempt:
                instance_health_info = requests.get(f"http://{unit_ip}:8008/health")
                assert instance_health_info.status_code == 200
    except RetryError:
        return False

    return True


async def secondary_up_to_date(ops_test: OpsTest, unit_name: str, expected_writes: int) -> bool:
    """Checks if secondary is up-to-date with the cluster.

    Retries over the period of one minute to give secondary adequate time to copy over data.
    """
    app = await app_name(ops_test)
    password = await get_password(ops_test, database_app_name=app)
    status = await ops_test.model.get_status()
    host = status["applications"][app]["units"][unit_name]["address"]
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
                    assert secondary_writes == expected_writes
    except RetryError:
        return False
    finally:
        connection.close()

    return True


async def send_signal_to_process(
    ops_test: OpsTest, unit_name: str, process: str, signal: str, use_ssh: bool = False
) -> None:
    """Send a signal to an OS process on a specific unit.

    Args:
        ops_test: The ops test framework instance
        unit_name: The name of the unit to run the command on
        process: OS process name
        signal: Signal that will be sent to the OS process
            (examples: SIGKILL, SIGTERM, SIGSTOP, SIGCONT)

    Returns:
        the command output if it succeeds, otherwise raises an exception.
    """
    # Killing or freezing the only instance can be disastrous.
    app = await app_name(ops_test)
    if len(ops_test.model.applications[app].units) < 2:
        await ops_test.model.applications[app].add_unit(count=1)
        await ops_test.model.wait_for_idle(apps=[app], status="active", timeout=1000)

    pod_name = unit_name.replace("/", "-")
    command = f"pkill --signal {signal} -f {process}"

    if use_ssh:
        kill_cmd = f"ssh {unit_name} {command}"
        return_code, _, _ = await asyncio.wait_for(ops_test.juju(*kill_cmd.split()), 10)
        if return_code != 0:
            raise ProcessError(
                "Expected command %s to succeed instead it failed: %s",
                command,
                return_code,
            )
        return

    # Load Kubernetes configuration to connect to the cluster.
    config.load_kube_config()

    # Send the signal.
    response = stream(
        core_v1_api.CoreV1Api().connect_get_namespaced_pod_exec,
        pod_name,
        ops_test.model.info.name,
        container="postgresql",
        command=command.split(),
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
        _preload_content=False,
    )

    response.run_forever(timeout=10)

    if response.returncode != 0:
        raise ProcessError(
            "Expected command %s to succeed instead it failed: %s",
            command,
            response.returncode,
        )


async def start_continuous_writes(ops_test: OpsTest, app: str) -> None:
    """Start continuous writes to PostgreSQL."""
    # Start the process by relating the application to the database or
    # by calling the action if the relation already exists.
    relations = [
        relation
        for relation in ops_test.model.applications[app].relations
        if not relation.is_peer
        and f"{relation.requires.application_name}:{relation.requires.name}"
        == "application:database"
    ]
    if not relations:
        await ops_test.model.relate(app, "application")
        await ops_test.model.wait_for_idle(status="active", timeout=1000)
    else:
        action = (
            await ops_test.model.applications["application"]
            .units[0]
            .run_action("start-continuous-writes")
        )
        await action.wait()
    for attempt in Retrying(stop=stop_after_delay(60 * 5), wait=wait_fixed(3), reraise=True):
        with attempt:
            action = (
                await ops_test.model.applications["application"]
                .units[0]
                .run_action("start-continuous-writes")
            )
            await action.wait()
            assert action.results["result"] == "True", "Unable to create continuous_writes table"


async def stop_continuous_writes(ops_test: OpsTest) -> int:
    """Stops continuous writes to PostgreSQL and returns the last written value."""
    action = await ops_test.model.units.get("application/0").run_action("stop-continuous-writes")
    action = await action.wait()
    return int(action.results["writes"])
