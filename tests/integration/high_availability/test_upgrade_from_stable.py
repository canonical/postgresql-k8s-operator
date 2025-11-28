# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import jubilant
from jubilant import Juju

from .high_availability_helpers_new import (
    check_db_units_writes_increment,
    count_switchovers,
    get_app_leader,
    run_upgrade,
    wait_for_apps_status,
)

DB_APP_NAME = "postgresql-k8s"
DB_TEST_APP_NAME = "postgresql-test-app"

MINUTE_SECS = 60

logging.getLogger("jubilant.wait").setLevel(logging.WARNING)


def test_deploy_stable(juju: Juju) -> None:
    """Simple test to ensure that the PostgreSQL and application charms get deployed."""
    logging.info("Deploying PostgreSQL cluster")
    juju.deploy(
        charm=DB_APP_NAME,
        app=DB_APP_NAME,
        base="ubuntu@24.04",
        # TODO Switch channel after stable release
        channel="16/edge",
        config={"profile": "testing"},
        num_units=3,
        trust=True,
    )
    juju.deploy(
        charm=DB_TEST_APP_NAME,
        app=DB_TEST_APP_NAME,
        base="ubuntu@22.04",
        channel="latest/edge",
        num_units=1,
    )

    juju.integrate(
        f"{DB_APP_NAME}:database",
        f"{DB_TEST_APP_NAME}:database",
    )

    logging.info("Wait for applications to become active")
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_NAME, DB_TEST_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )


def test_pre_refresh_check(juju: Juju) -> None:
    """Test that the pre-refresh-check action runs successfully."""
    db_leader = get_app_leader(juju, DB_APP_NAME)

    logging.info("Run pre-refresh-check action")
    juju.run(unit=db_leader, action="pre-refresh-check")

    juju.wait(jubilant.all_agents_idle, timeout=5 * MINUTE_SECS)


def test_upgrade_from_stable(juju: Juju, charm: str, continuous_writes) -> None:
    """Update the second cluster."""
    logging.info("Ensure continuous writes are incrementing")
    check_db_units_writes_increment(juju, DB_APP_NAME)

    initial_number_of_switchovers = count_switchovers(juju, DB_APP_NAME)

    run_upgrade(juju, DB_APP_NAME, charm)

    logging.info("Ensure continuous writes are incrementing")
    check_db_units_writes_increment(juju, DB_APP_NAME)

    logging.info("checking the number of switchovers")
    final_number_of_switchovers = count_switchovers(juju, DB_APP_NAME)
    assert (final_number_of_switchovers - initial_number_of_switchovers) <= 2, (
        "Number of switchovers is greater than 2"
    )
