# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio
import logging
from asyncio import gather
from pathlib import Path

import pytest
import yaml
from pytest_operator.plugin import OpsTest

from .. import markers
from ..helpers import (
    CHARM_BASE,
)

logger = logging.getLogger(__name__)

APPLICATION_APP_NAME = "postgresql-test-app"
DATABASE_APP_NAME = "database"
ANOTHER_DATABASE_APP_NAME = "another-database"
DATA_INTEGRATOR_APP_NAME = "data-integrator"
DISCOURSE_APP_NAME = "discourse-k8s"
REDIS_APP_NAME = "redis-k8s"
APP_NAMES = [APPLICATION_APP_NAME, DATABASE_APP_NAME, ANOTHER_DATABASE_APP_NAME]
DATABASE_APP_METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
FIRST_DATABASE_RELATION_NAME = "database"
SECOND_DATABASE_RELATION_NAME = "second-database"
MULTIPLE_DATABASE_CLUSTERS_RELATION_NAME = "multiple-database-clusters"
ALIASED_MULTIPLE_DATABASE_CLUSTERS_RELATION_NAME = "aliased-multiple-database-clusters"
NO_DATABASE_RELATION_NAME = "no-database"
INVALID_EXTRA_USER_ROLE_BLOCKING_MESSAGE = "invalid role(s) for extra user roles"


@pytest.mark.abort_on_fail
async def test_database_deploy_clientapps(ops_test: OpsTest, charm):
    """Test basic functionality of database relation interface."""
    # Deploy both charms (multiple units for each application to test that later they correctly
    # set data in the relation application databag using only the leader unit).
    async with ops_test.fast_forward():
        await asyncio.gather(
            ops_test.model.deploy(
                charm,
                resources={
                    "postgresql-image": DATABASE_APP_METADATA["resources"]["postgresql-image"][
                        "upstream-source"
                    ]
                },
                application_name=DATABASE_APP_NAME,
                num_units=3,
                base=CHARM_BASE,
                trust=True,
                config={"profile": "testing"},
            ),
        )
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME],
            status="active",
            raise_on_blocked=True,
            raise_on_error=False,
            timeout=1000,
        )


@markers.amd64_only  # discourse-k8s charm not available for arm64
async def test_discourse(ops_test: OpsTest):
    pytest.skip("Second migration doesn't complete")
    # Deploy Discourse and Redis.
    await gather(
        ops_test.model.deploy(DISCOURSE_APP_NAME, application_name=DISCOURSE_APP_NAME),
        ops_test.model.deploy(
            REDIS_APP_NAME, application_name=REDIS_APP_NAME, channel="latest/edge", base=CHARM_BASE
        ),
    )

    async with ops_test.fast_forward():
        # Enable the plugins/extensions required by Discourse.
        logger.info("Enabling the plugins/extensions required by Discourse")
        config = {"plugin_hstore_enable": "True", "plugin_pg_trgm_enable": "True"}
        await ops_test.model.applications[DATABASE_APP_NAME].set_config(config)
        await gather(
            ops_test.model.wait_for_idle(apps=[DISCOURSE_APP_NAME], status="waiting"),
            ops_test.model.wait_for_idle(
                apps=[DATABASE_APP_NAME, REDIS_APP_NAME], status="active"
            ),
        )
        # Add both relations to Discourse (PostgreSQL and Redis)
        # and wait for it to be ready.
        logger.info("Adding relations")
        await gather(
            ops_test.model.add_relation(DATABASE_APP_NAME, DISCOURSE_APP_NAME),
            ops_test.model.add_relation(REDIS_APP_NAME, DISCOURSE_APP_NAME),
        )
        await gather(
            ops_test.model.wait_for_idle(apps=[DISCOURSE_APP_NAME], timeout=2000),
            ops_test.model.wait_for_idle(
                apps=[DATABASE_APP_NAME, REDIS_APP_NAME], status="active"
            ),
        )
        logger.info("Configuring Discourse")
        config = {
            "developer_emails": "noreply@canonical.com",
            "external_hostname": "discourse-k8s",
            "smtp_address": "test.local",
            "smtp_domain": "test.local",
            "s3_install_cors_rule": "false",
        }
        await ops_test.model.applications[DISCOURSE_APP_NAME].set_config(config)
        await ops_test.model.wait_for_idle(apps=[DISCOURSE_APP_NAME], status="active")

        # Deploy a new discourse application (https://github.com/canonical/data-platform-libs/issues/118
        # prevents from re-relating the same Discourse application; Discourse uses the old secret and fails).
        await ops_test.model.applications[DISCOURSE_APP_NAME].remove()
        other_discourse_app_name = f"other-{DISCOURSE_APP_NAME}"
        await ops_test.model.deploy(DISCOURSE_APP_NAME, application_name=other_discourse_app_name)

        # Add both relations to Discourse (PostgreSQL and Redis)
        # and wait for it to be ready.
        logger.info("Adding relations")
        await gather(
            ops_test.model.add_relation(DATABASE_APP_NAME, other_discourse_app_name),
            ops_test.model.add_relation(REDIS_APP_NAME, other_discourse_app_name),
        )
        await gather(
            ops_test.model.wait_for_idle(apps=[other_discourse_app_name], timeout=2000),
            ops_test.model.wait_for_idle(
                apps=[DATABASE_APP_NAME, REDIS_APP_NAME], status="active"
            ),
        )
        logger.info("Configuring Discourse")
        config = {
            "developer_emails": "noreply@canonical.com",
            "external_hostname": "discourse-k8s",
            "smtp_address": "test.local",
            "smtp_domain": "test.local",
            "s3_install_cors_rule": "false",
        }
        await ops_test.model.applications[other_discourse_app_name].set_config(config)
        await ops_test.model.wait_for_idle(apps=[other_discourse_app_name], status="active")


@markers.amd64_only  # indico charm not available for arm64
async def test_indico_datatabase(ops_test: OpsTest) -> None:
    """Tests deploying and relating to the Indico charm."""
    async with ops_test.fast_forward(fast_interval="30s"):
        await ops_test.model.deploy(
            "indico",
            channel="latest/edge",
            application_name="indico",
            num_units=1,
            series="focal",
        )
        await ops_test.model.deploy(
            "redis-k8s", channel="edge", application_name="redis-broker", base="ubuntu@20.04"
        )
        await ops_test.model.deploy(
            "redis-k8s", channel="edge", application_name="redis-cache", base="ubuntu@20.04"
        )
        await asyncio.gather(
            ops_test.model.relate("redis-broker", "indico:redis-broker"),
            ops_test.model.relate("redis-cache", "indico:redis-cache"),
        )

        # Wait for model to stabilise
        await ops_test.model.wait_for_idle(
            apps=["indico"],
            status="waiting",
            timeout=1000,
        )

        # Verify that the charm doesn't block when the extensions are enabled.
        logger.info("Verifying that the charm doesn't block when the extensions are enabled")
        config = {"plugin_pg_trgm_enable": "True", "plugin_unaccent_enable": "True"}
        await ops_test.model.applications[DATABASE_APP_NAME].set_config(config)
        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active")
        await ops_test.model.relate(DATABASE_APP_NAME, "indico")
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME, "indico"],
            status="active",
            timeout=2000,
        )
