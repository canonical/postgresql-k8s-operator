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


def test_publisher_id_at_root(harness: Harness):
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
    harness.update_relation_data(rel_id, harness.charm.app.name, {
        "secret-id": "dummy-secret",
    })

    # Trigger another change to ensure processing
    harness.update_relation_data(rel_id, "remote-subscriber", {
        "subscription-request": json.dumps({
            "testdb": ["public.t1", "public.t2"],
        }),
    })

    app_data = harness.get_relation_data(rel_id, harness.charm.app.name)

    # Root-level publisher-id must be set
    expected_identity = f"{harness.model.uuid}:{harness.charm.app.name}"
    assert app_data.get("publisher-id") == expected_identity

    # Publications should not contain publisher-id entries anymore
    publications = json.loads(app_data.get("publications", "{}"))
    for db, pub in publications.items():
        assert "publisher-id" not in pub
