#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.


import logging
import re
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
    await ops_test.model.deploy(charm, resources=resources, application_name=APP_NAME)

    # Issuing dummy update_status just to trigger an event.
    await ops_test.model.set_config({"update-status-hook-interval": "10s"})

    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)
    assert ops_test.model.applications[APP_NAME].units[0].workload_status == "active"

    # Effectively disable the update status from firing.
    await ops_test.model.set_config({"update-status-hook-interval": "60m"})


@pytest.mark.abort_on_fail
async def test_database_is_up(ops_test: OpsTest):
    status = await ops_test.model.get_status()  # noqa: F821
    host = status["applications"][APP_NAME]["units"][f"{APP_NAME}/0"]["address"]

    # Retrieving the postgres user password from the stored state.
    state = await ops_test.juju("run", "--unit", f"{APP_NAME}/0", "state-get")
    password = re.search("(?<=postgres_password: ).*?(?=})", state[1]).group(0)

    logger.info("connecting to the database host: %s", host)
    connection = psycopg2.connect(
        f"dbname='postgres' user='postgres' host='{host}' password='{password}' connect_timeout=1"
    )
    assert connection.status == psycopg2.extensions.STATUS_READY
    connection.close()
