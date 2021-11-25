#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.


import logging
from pathlib import Path

import psycopg2
import pytest
import yaml
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    """Build the charm-under-test and deploy it.

    Assert on the unit status before any relations/configurations take place.
    """
    # Build and deploy charm from local source folder.
    charm = await ops_test.build_charm(".")
    resources = {"postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"]}
    # Deploy two units in order to test later the sharing of password through peer relation data.
    await ops_test.model.deploy(charm, resources=resources, application_name=APP_NAME, num_units=2)

    # Issuing dummy update_status just to trigger an event.
    await ops_test.model.set_config({"update-status-hook-interval": "10s"})

    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)
    assert ops_test.model.applications[APP_NAME].units[0].workload_status == "active"

    # Effectively disable the update status from firing.
    await ops_test.model.set_config({"update-status-hook-interval": "60m"})


@pytest.mark.abort_on_fail
async def test_database_is_up(ops_test: OpsTest):
    # Retrieving the postgres user password using the action.
    action = await ops_test.model.units.get(f"{APP_NAME}/0").run_action("get-postgres-password")
    action = await action.wait()
    password = action.results["postgres-password"]

    # Testing the connection to each PostgreSQL instance.
    status = await ops_test.model.get_status()  # noqa: F821
    for _, unit in status["applications"][APP_NAME]["units"].items():
        host = unit["address"]
        logger.info("connecting to the database host: %s", host)
        connection = psycopg2.connect(
            f"dbname='postgres' user='postgres' host='{host}' password='{password}' connect_timeout=1"
        )
        assert connection.status == psycopg2.extensions.STATUS_READY
        connection.close()
