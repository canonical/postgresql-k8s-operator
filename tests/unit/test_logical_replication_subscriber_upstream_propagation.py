# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import json
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
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

    # Patch the postgresql property to return our mock
    patcher = patch.object(type(h.charm), "postgresql", new_callable=PropertyMock, return_value=pg)
    patcher.start()

    # Mock the _create_user method to avoid database connection
    h.charm.logical_replication._create_user = MagicMock(return_value=("user", "pass"))

    # Mock update_config to avoid K8s API access
    h.charm.update_config = MagicMock()

    yield h
    patcher.stop()


def test_offer_upstream_updates_when_subscribed_upstream_changes(harness: Harness):
    # Create offer relation where we are publisher and remote is subscriber
    rel_id = harness.add_relation("logical-replication-offer", "remote-subscriber")

    # Create a secret for this relation (owned by us, so no grant needed)
    secret_id = harness.add_model_secret(
        owner=harness.charm.app.name,
        content={"username": "user", "password": "pass", "primary": "host:5432"},
    )

    # Set secret-id in app bag before adding unit
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
                "testdb": ["public.t1"],
            }),
            "requester-id": "some:requester",
        },
    )

    # Trigger another change to ensure processing of the offer
    harness.update_relation_data(
        rel_id,
        "remote-subscriber",
        {
            "subscription-request": json.dumps({
                "testdb": ["public.t1"],
            }),
        },
    )

    # Initially, upstream for requested table should be our own identity
    app_data = harness.get_relation_data(rel_id, harness.charm.app.name)
    initial_upstream = json.loads(app_data.get("upstream", "{}"))
    self_identity = f"{harness.model.uuid}:{harness.charm.app.name}"
    assert initial_upstream.get("testdb:public.t1") == self_identity

    # Simulate that we (as this app) now subscribe to an upstream for the same table
    new_upstream_identity = "model-x:app-y"
    mapping = {"testdb:public.t1": new_upstream_identity}
    harness.charm.app_peer_data["logical-replication-subscribed-upstream"] = json.dumps(mapping)

    # Trigger propagation directly (instead of rebuild which would overwrite our manual mapping)
    harness.charm.logical_replication._propagate_upstream_to_offers(
        mapping, set_configured_time=False
    )

    # Verify offer relation's upstream updated to reflect new upstream identity
    app_data2 = harness.get_relation_data(rel_id, harness.charm.app.name)
    upstream2 = json.loads(app_data2.get("upstream", "{}"))
    assert upstream2.get("testdb:public.t1") == new_upstream_identity
