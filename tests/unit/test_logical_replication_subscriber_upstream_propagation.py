# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import json
from unittest.mock import MagicMock

import pytest
from ops.testing import Harness

from charm import PostgresqlOperatorCharm


@pytest.fixture()
def harness():
    h = Harness(PostgresqlOperatorCharm)
    h.set_leader(True)
    h.begin()
    # Mock primary endpoint existing
    type(h.charm).primary_endpoint = property(lambda self: "host:5432")
    # Mock postgresql API used by logical replication
    pg = MagicMock()
    pg.database_exists.return_value = True
    pg.table_exists.return_value = True
    pg.is_table_empty.return_value = True
    h.charm._postgresql = pg  # use property in charm.postgresql
    return h


def test_offer_upstream_updates_when_subscribed_upstream_changes(harness: Harness):
    # Create offer relation where we are publisher and remote is subscriber
    rel_id = harness.add_relation("logical-replication-offer", "remote-subscriber")
    harness.add_relation_unit(rel_id, "remote-subscriber/0")

    # Remote subscriber requests a table
    harness.update_relation_data(rel_id, "remote-subscriber", {
        "subscription-request": json.dumps({
            "testdb": ["public.t1"],
        }),
        "requester-id": "some:requester",
    })

    # Our charm needs secret-id in our app bag (simulated by joined hook in code)
    harness.update_relation_data(rel_id, harness.charm.app.name, {
        "secret-id": "dummy-secret",
    })

    # Trigger another change to ensure processing of the offer
    harness.update_relation_data(rel_id, "remote-subscriber", {
        "subscription-request": json.dumps({
            "testdb": ["public.t1"],
        }),
    })

    # Initially, upstream for requested table should be our own identity
    app_data = harness.get_relation_data(rel_id, harness.charm.app.name)
    initial_upstream = json.loads(app_data.get("upstream", "{}"))
    self_identity = f"{harness.model.uuid}:{harness.charm.app.name}"
    assert initial_upstream.get("testdb:public.t1") == self_identity

    # Simulate that we (as this app) now subscribe to an upstream for the same table
    new_upstream_identity = "model-x:app-y"
    mapping = {"testdb:public.t1": new_upstream_identity}
    harness.charm.app_peer_data["logical-replication-subscribed-upstream"] = json.dumps(mapping)

    # Trigger rebuild which should also propagate to offer relation's root-level upstream
    harness.charm.logical_replication._rebuild_subscribed_upstream()

    # Verify offer relation's upstream updated to reflect new upstream identity
    app_data2 = harness.get_relation_data(rel_id, harness.charm.app.name)
    upstream2 = json.loads(app_data2.get("upstream", "{}"))
    assert upstream2.get("testdb:public.t1") == new_upstream_identity
