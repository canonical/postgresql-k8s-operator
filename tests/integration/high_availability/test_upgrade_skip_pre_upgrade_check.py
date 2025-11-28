# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import jubilant
from jubilant import Juju
from tenacity import Retrying, stop_after_attempt, wait_fixed

from .high_availability_helpers_new import (
    check_db_units_writes_increment,
    count_switchovers,
    get_app_leader,
    get_app_units,
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


def test_refresh_without_pre_refresh_check(juju: Juju, charm: str, continuous_writes) -> None:
    """Test updating from stable channel."""
    initial_number_of_switchovers = count_switchovers(juju, DB_APP_NAME)

    run_upgrade(juju, DB_APP_NAME, charm)

    logging.info("Ensure continuous writes are incrementing")
    check_db_units_writes_increment(juju, DB_APP_NAME)

    logging.info("checking the number of switchovers")
    final_number_of_switchovers = count_switchovers(juju, DB_APP_NAME)
    assert (final_number_of_switchovers - initial_number_of_switchovers) <= 2, (
        "Number of switchovers is greater than 2"
    )


async def test_rollback_without_pre_refresh_check(
    juju: Juju, charm: str, continuous_writes
) -> None:
    """Test refresh back to stable channel."""
    # Early Jubilant 1.X.Y versions do not support the `switch` option
    logging.info("Refresh the charm to stable channel")
    juju.cli("refresh", "--channel=16/edge", f"--switch={DB_APP_NAME}", DB_APP_NAME)

    logging.info("Wait for refresh to block as paused or incompatible")
    units = get_app_units(juju, DB_APP_NAME)
    unit_names = sorted(units.keys())
    try:
        juju.wait(
            lambda status: status.apps[DB_APP_NAME].units[unit_names[-1]].is_blocked,
            timeout=5 * MINUTE_SECS,
        )
        juju.wait(jubilant.all_agents_idle, timeout=5 * MINUTE_SECS)

        if (
            "Refresh incompatible"
            in juju.status().apps[DB_APP_NAME].units[unit_names[-1]].workload_status.message
        ):
            logging.info("Application refresh is blocked due to incompatibility")
            juju.run(
                unit=unit_names[-1],
                action="force-refresh-start",
                params={"check-compatibility": False, "run-pre-refresh-checks": False},
                wait=5 * MINUTE_SECS,
            )
    except TimeoutError:
        logging.info("Upgrade completed without incompatibility")
        assert juju.status().apps[DB_APP_NAME].is_active

    juju.wait(jubilant.all_agents_idle, timeout=5 * MINUTE_SECS)

    logging.info("Run resume-refresh action")
    for attempt in Retrying(reraise=True, stop=stop_after_attempt(3), wait=wait_fixed(3)):
        with attempt:
            juju.run(
                unit=get_app_leader(juju, DB_APP_NAME),
                action="resume-refresh",
                wait=5 * MINUTE_SECS,
            )

    logging.info("Wait for upgrade to complete")
    juju.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_NAME),
        timeout=20 * MINUTE_SECS,
    )

    check_db_units_writes_increment(juju, DB_APP_NAME)
