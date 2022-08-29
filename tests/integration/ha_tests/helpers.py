# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

from pathlib import Path
from typing import Optional

import yaml
from pytest_operator.plugin import OpsTest

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


async def get_password(ops_test: OpsTest, app) -> str:
    """Use the charm action to retrieve the password from provided application.

    Returns:
        string with the password stored on the peer relation databag.
    """
    # Can retrieve from any unit running unit, so we pick the first.
    unit_name = ops_test.model.applications[app].units[0].name
    action = await ops_test.model.units.get(unit_name).run_action("get-operator-password")
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


async def kill_process(ops_test: OpsTest, unit_name: str, process: str, kill_code: str):
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
