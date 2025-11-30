# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import json
from unittest.mock import MagicMock, patch

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
    type(h.charm).primary_endpoint = property(lambda self: "host:5432")
    # Mock postgresql API used by logical replication
    pg = MagicMock()
    pg.database_exists.return_value = True
    pg.table_exists.return_value = True
    pg.is_table_empty.return_value = True
    h.charm._postgresql = pg  # use property in charm.postgresql
    return h


def _identity(h: Harness) -> str:
    return f"{h.model.uuid}:{h.charm.app.name}"


def test_subscriber_blocks_on_cycle(harness: Harness):
    # Create logical-replication relation where remote is publisher
    rel_id = harness.add_relation("logical-replication", "remote-pg")
    harness.add_relation_unit(rel_id, "remote-pg/0")

    # Publisher provides secret-id and upstream provenance mapping
    # Mark the upstream of our requested table as our own identity to simulate C->A block
    upstream = {"testdb:public.test_cycle": _identity(harness)}
    harness.update_relation_data(rel_id, "remote-pg", {
        "secret-id": "dummy",
        "publications": json.dumps({}),
        "upstream": json.dumps(upstream),
        "errors": json.dumps([]),
    })

    # Provide subscription request config
    harness.update_config({
        "logical_replication_subscription_request": json.dumps({
            "testdb": ["public.test_cycle"],
        })
    })

    # Validation is expected to fail and set BlockedStatus
    assert isinstance(harness.model.unit.status, BlockedStatus)


def test_offer_publishes_root_upstream_map(harness: Harness):
    # Create offer relation where we are publisher and remote is subscriber
    rel_id = harness.add_relation("logical-replication-offer", "remote-subscriber")
    harness.add_relation_unit(rel_id, "remote-subscriber/0")

    # Remote subscriber requests a table
    harness.update_relation_data(rel_id, "remote-subscriber", {
        "subscription-request": json.dumps({
            "testdb": ["public.t1", "public.t2"],
        }),
        "requester-id": "some:requester",
    })

    # Our charm needs secret-id to be present in our app bag (simulated by joined hook in code)
    # We directly set it and then trigger relation-changed again to process offer
    harness.update_relation_data(rel_id, harness.charm.app.name, {
        "secret-id": "dummy-secret",
    })

    # Trigger another change to ensure processing
    harness.update_relation_data(rel_id, "remote-subscriber", {
        "subscription-request": json.dumps({
            "testdb": ["public.t1", "public.t2"],
        }),
    })

    # Check that root-level upstream map exists and contains our identity for requested tables
    app_data = harness.get_relation_data(rel_id, harness.charm.app.name)
    upstream = json.loads(app_data.get("upstream", "{}"))
    assert upstream.get("testdb:public.t1") == _identity(harness)
    assert upstream.get("testdb:public.t2") == _identity(harness)
