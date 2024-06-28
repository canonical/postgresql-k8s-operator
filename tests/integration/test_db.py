#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
from asyncio import gather

import pytest
from pytest_operator.plugin import OpsTest

from . import markers
from .helpers import (
    APPLICATION_NAME,
    CHARM_SERIES,
    DATABASE_APP_NAME,
    build_and_deploy,
    check_database_creation,
    check_database_users_existence,
    deploy_and_relate_application_with_postgresql,
    get_leader_unit,
    wait_for_relation_removed_between,
)

EXTENSIONS_BLOCKING_MESSAGE = "extensions requested through relation"
FINOS_WALTZ_APP_NAME = "finos-waltz"
ANOTHER_FINOS_WALTZ_APP_NAME = "another-finos-waltz"
APPLICATION_UNITS = 1
DATABASE_UNITS = 3
ROLES_BLOCKING_MESSAGE = (
    "roles requested through relation, use postgresql_client interface instead"
)

logger = logging.getLogger(__name__)


@pytest.mark.group(1)
@markers.amd64_only  # finos-waltz-k8s charm not available for arm64
async def test_finos_waltz_db(ops_test: OpsTest) -> None:
    """Deploy Finos Waltz to test the 'db' relation.

    Args:
        ops_test: The ops test framework
    """
    async with ops_test.fast_forward():
        # Build and deploy the PostgreSQL charm.
        await build_and_deploy(ops_test, DATABASE_UNITS)

        assert len(ops_test.model.applications[DATABASE_APP_NAME].units) == DATABASE_UNITS

        for unit in ops_test.model.applications[DATABASE_APP_NAME].units:
            assert unit.workload_status == "active"

        # Deploy and test the first deployment of Finos Waltz.
        relation_id = await deploy_and_relate_application_with_postgresql(
            ops_test, "finos-waltz-k8s", FINOS_WALTZ_APP_NAME, APPLICATION_UNITS, channel="edge"
        )
        await check_database_creation(ops_test, "waltz")

        finos_waltz_users = [f"relation_id_{relation_id}"]

        await check_database_users_existence(ops_test, finos_waltz_users, [])

        # Deploy and test another deployment of Finos Waltz.
        another_relation_id = await deploy_and_relate_application_with_postgresql(
            ops_test,
            "finos-waltz-k8s",
            ANOTHER_FINOS_WALTZ_APP_NAME,
            APPLICATION_UNITS,
            channel="edge",
        )
        # In this case, the database name is the same as in the first deployment
        # because it's a fixed value in Finos Waltz charm.
        await check_database_creation(ops_test, "waltz")

        another_finos_waltz_users = [f"relation_id_{another_relation_id}"]

        await check_database_users_existence(
            ops_test, finos_waltz_users + another_finos_waltz_users, []
        )

        # Remove second relation and validate that related users were deleted
        await ops_test.model.applications[DATABASE_APP_NAME].remove_relation(
            f"{DATABASE_APP_NAME}:db", f"{ANOTHER_FINOS_WALTZ_APP_NAME}"
        )
        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1000)
        await check_database_users_existence(
            ops_test, finos_waltz_users, another_finos_waltz_users
        )

        # Remove first relation and validate that related users were deleted
        await ops_test.model.applications[DATABASE_APP_NAME].remove_relation(
            f"{DATABASE_APP_NAME}:db", f"{FINOS_WALTZ_APP_NAME}"
        )
        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1000)
        await check_database_users_existence(ops_test, [], finos_waltz_users)

        # Remove the first and second deployment of Finos Waltz.
        await ops_test.model.remove_application(FINOS_WALTZ_APP_NAME, block_until_done=True)
        await ops_test.model.remove_application(
            ANOTHER_FINOS_WALTZ_APP_NAME, block_until_done=True
        )


@pytest.mark.group(1)
@markers.amd64_only  # finos-waltz-k8s charm not available for arm64
# (and this test depends on previous test with finos-waltz-k8s charm)
async def test_extensions_blocking(ops_test: OpsTest) -> None:
    await ops_test.model.deploy(
        APPLICATION_NAME,
        application_name=APPLICATION_NAME,
        series=CHARM_SERIES,
        channel="edge",
    )
    await ops_test.model.deploy(
        APPLICATION_NAME,
        application_name=f"{APPLICATION_NAME}2",
        series=CHARM_SERIES,
        channel="edge",
    )

    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME, APPLICATION_NAME, f"{APPLICATION_NAME}2"],
        status="active",
        timeout=1000,
    )

    await gather(
        ops_test.model.relate(f"{DATABASE_APP_NAME}:db", f"{APPLICATION_NAME}:db"),
        ops_test.model.relate(f"{DATABASE_APP_NAME}:db", f"{APPLICATION_NAME}2:db"),
    )

    leader_unit = await get_leader_unit(ops_test, DATABASE_APP_NAME)
    await ops_test.model.block_until(
        lambda: leader_unit.workload_status_message == EXTENSIONS_BLOCKING_MESSAGE, timeout=1000
    )

    assert leader_unit.workload_status_message == EXTENSIONS_BLOCKING_MESSAGE

    logger.info("Verify that the charm remains blocked if there are other blocking relations")
    await ops_test.model.applications[DATABASE_APP_NAME].destroy_relation(
        f"{DATABASE_APP_NAME}:db", f"{APPLICATION_NAME}:db"
    )

    await ops_test.model.block_until(
        lambda: leader_unit.workload_status_message == EXTENSIONS_BLOCKING_MESSAGE, timeout=1000
    )

    assert leader_unit.workload_status_message == EXTENSIONS_BLOCKING_MESSAGE

    logger.info("Verify that active status is restored when all blocking relations are gone")
    await ops_test.model.applications[DATABASE_APP_NAME].destroy_relation(
        f"{DATABASE_APP_NAME}:db", f"{APPLICATION_NAME}2:db"
    )

    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME],
        status="active",
        timeout=1000,
    )

    logger.info("Verifying that the charm doesn't block when the extensions are enabled")
    config = {"plugin_pg_trgm_enable": "True", "plugin_unaccent_enable": "True"}
    await ops_test.model.applications[DATABASE_APP_NAME].set_config(config)
    await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active")
    await ops_test.model.relate(f"{DATABASE_APP_NAME}:db", f"{APPLICATION_NAME}:db")
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME, APPLICATION_NAME],
        status="active",
        timeout=2000,
    )

    logger.info("Verifying that the charm unblocks when the extensions are enabled")
    config = {"plugin_pg_trgm_enable": "False", "plugin_unaccent_enable": "False"}
    await ops_test.model.applications[DATABASE_APP_NAME].set_config(config)
    await ops_test.model.applications[DATABASE_APP_NAME].destroy_relation(
        f"{DATABASE_APP_NAME}:db", f"{APPLICATION_NAME}:db"
    )
    wait_for_relation_removed_between(ops_test, DATABASE_APP_NAME, APPLICATION_NAME)
    await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME, APPLICATION_NAME], status="active")

    await ops_test.model.relate(f"{DATABASE_APP_NAME}:db", f"{APPLICATION_NAME}:db")
    await ops_test.model.block_until(
        lambda: leader_unit.workload_status_message == EXTENSIONS_BLOCKING_MESSAGE, timeout=1000
    )

    config = {"plugin_pg_trgm_enable": "True", "plugin_unaccent_enable": "True"}
    await ops_test.model.applications[DATABASE_APP_NAME].set_config(config)
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME, APPLICATION_NAME],
        status="active",
        raise_on_blocked=False,
        timeout=2000,
    )
    # removing relation to test roles
    await ops_test.model.applications[DATABASE_APP_NAME].destroy_relation(
        f"{DATABASE_APP_NAME}:db", f"{APPLICATION_NAME}:db"
    )


@pytest.mark.group(1)
@markers.amd64_only  # finos-waltz-k8s charm not available for arm64
# (and this test depends on a previous test with finos-waltz-k8s charm)
async def test_roles_blocking(ops_test: OpsTest) -> None:
    config = {"legacy_roles": "true"}
    await ops_test.model.applications[APPLICATION_NAME].set_config(config)
    await ops_test.model.applications[f"{APPLICATION_NAME}2"].set_config(config)
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME, APPLICATION_NAME, f"{APPLICATION_NAME}2"],
        status="active",
    )

    await gather(
        ops_test.model.relate(f"{DATABASE_APP_NAME}:db", f"{APPLICATION_NAME}:db"),
        ops_test.model.relate(f"{DATABASE_APP_NAME}:db", f"{APPLICATION_NAME}2:db"),
    )

    leader_unit = await get_leader_unit(ops_test, DATABASE_APP_NAME)
    await ops_test.model.block_until(
        lambda: leader_unit.workload_status_message == ROLES_BLOCKING_MESSAGE, timeout=1000
    )

    assert leader_unit.workload_status_message == ROLES_BLOCKING_MESSAGE

    logger.info("Verify that the charm remains blocked if there are other blocking relations")
    await ops_test.model.applications[DATABASE_APP_NAME].destroy_relation(
        f"{DATABASE_APP_NAME}:db", f"{APPLICATION_NAME}:db"
    )

    await ops_test.model.block_until(
        lambda: leader_unit.workload_status_message == ROLES_BLOCKING_MESSAGE, timeout=1000
    )

    assert leader_unit.workload_status_message == ROLES_BLOCKING_MESSAGE

    logger.info("Verify that active status is restored when all blocking relations are gone")
    await ops_test.model.applications[DATABASE_APP_NAME].destroy_relation(
        f"{DATABASE_APP_NAME}:db", f"{APPLICATION_NAME}2:db"
    )

    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME],
        status="active",
        timeout=1000,
    )
