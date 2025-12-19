# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import json
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from ops.model import BlockedStatus
from ops.testing import Harness

from charm import PostgresqlOperatorCharm


@pytest.fixture()
def harness():
    h = Harness(PostgresqlOperatorCharm)
    h.set_leader(True)
    h.begin()
    # Mock primary endpoint existing
    type(h.charm).primary_endpoint = PropertyMock(return_value="host:5432")
    # Mock postgresql API used by logical replication
    pg = MagicMock()
    pg.database_exists.return_value = True
    pg.table_exists.return_value = True
    pg.is_table_empty.return_value = True
    pg.create_user = MagicMock()
    pg.create_publication = MagicMock()
    pg.publication_exists.return_value = False
    pg.grant_replication_privileges = MagicMock()
    pg.revoke_replication_privileges = MagicMock()
    pg.delete_user = MagicMock()
    pg.drop_publication = MagicMock()
    pg.refresh_subscription = MagicMock()
    pg.create_subscription = MagicMock()
    pg.drop_subscription = MagicMock()
    pg.update_subscription = MagicMock()
    pg.alter_publication = MagicMock()

    # Patch the postgresql property to return our mock
    patcher = patch.object(type(h.charm), "postgresql", new_callable=PropertyMock, return_value=pg)
    patcher.start()

    # Mock the _create_user method to avoid database connection
    h.charm.logical_replication._create_user = MagicMock(return_value=("user", "pass"))

    # Mock update_config to avoid K8s API access
    h.charm.update_config = MagicMock()

    yield h
    patcher.stop()


def _identity(h: Harness) -> str:
    return f"{h.model.uuid}:{h.charm.app.name}"


def test_subscriber_blocks_on_cycle(harness: Harness):
    # Provide subscription request config FIRST (before creating relation)
    with harness.hooks_disabled():
        harness.update_config({
            "logical_replication_subscription_request": json.dumps({
                "testdb": ["public.test_cycle"],
            })
        })

    # Create logical-replication relation where remote is publisher
    rel_id = harness.add_relation("logical-replication", "remote-pg")
    harness.add_relation_unit(rel_id, "remote-pg/0")

    # Create a secret for the logical-replication relation owned by remote
    secret_id = harness.add_model_secret(
        owner="remote-pg", content={"username": "user", "password": "pass", "primary": "host:5432"}
    )
    # Grant access to the secret for this charm
    harness.grant_secret(secret_id, harness.charm.app.name)

    # Publisher provides secret-id and upstream provenance mapping
    # Mark the upstream of our requested table as our own identity to simulate C->A block
    upstream = {"testdb:public.test_cycle": _identity(harness)}
    harness.update_relation_data(
        rel_id,
        "remote-pg",
        {
            "secret-id": secret_id,
            "publications": json.dumps({}),
            "upstream": json.dumps(upstream),
            "errors": json.dumps([]),
        },
    )

    # Manually trigger validation now that upstream is set
    harness.charm.logical_replication._validate_subscription_request()

    # Validation is expected to fail and set BlockedStatus
    assert isinstance(harness.model.unit.status, BlockedStatus)


def test_offer_publishes_root_upstream_map(harness: Harness):
    # Create offer relation where we are publisher and remote is subscriber
    rel_id = harness.add_relation("logical-replication-offer", "remote-subscriber")

    # Create a secret for this relation (owned by us, so no grant needed)
    secret_id = harness.add_model_secret(
        owner=harness.charm.app.name,
        content={"username": "user", "password": "pass", "primary": "host:5432"},
    )

    # Set secret-id in app bag before adding unit (to simulate what _on_offer_relation_joined does)
    harness.update_relation_data(
        rel_id,
        harness.charm.app.name,
        {
            "secret-id": secret_id,
        },
    )

    harness.add_relation_unit(rel_id, "remote-subscriber/0")

    # Remote subscriber requests a table
    harness.update_relation_data(
        rel_id,
        "remote-subscriber",
        {
            "subscription-request": json.dumps({
                "testdb": ["public.t1", "public.t2"],
            }),
            "requester-id": "some:requester",
        },
    )

    # Trigger another change to ensure processing
    harness.update_relation_data(
        rel_id,
        "remote-subscriber",
        {
            "subscription-request": json.dumps({
                "testdb": ["public.t1", "public.t2"],
            }),
        },
    )

    # Check that root-level upstream map exists and contains our identity for requested tables
    app_data = harness.get_relation_data(rel_id, harness.charm.app.name)
    upstream = json.loads(app_data.get("upstream", "{}"))
    assert upstream.get("testdb:public.t1") == _identity(harness)
    assert upstream.get("testdb:public.t2") == _identity(harness)
