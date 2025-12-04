# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import json
from unittest.mock import patch

import pytest
from ops.testing import Harness

from charm import PostgresqlOperatorCharm
from constants import PEER


@pytest.fixture(autouse=True)
def harness():
    """Create a test harness for the PostgreSQL charm."""
    harness = Harness(PostgresqlOperatorCharm)
    harness.set_leader(True)
    harness.begin()

    # Add peer relation
    with harness.hooks_disabled():
        harness.add_relation(PEER, harness.charm.app.name)

    yield harness
    harness.cleanup()


def test_would_create_circular_replication_no_relation(harness):
    """Test circular detection when there's no subscription relation."""
    with patch("charm.Patroni.get_primary"):
        result = harness.charm.logical_replication._would_create_circular_replication(
            None, "testdb", "public.test_table"
        )
        assert result is False


def test_would_create_circular_replication_no_database_published(harness):
    """Test circular detection when database is not published yet."""
    with (
        patch("charm.Patroni.get_primary"),
        harness.hooks_disabled(),
    ):
        # Create a logical replication relation
        rel_id = harness.add_relation("logical-replication", "remote-app")
        relation = harness.model.get_relation("logical-replication", rel_id)

        # Set empty publications
        harness.update_relation_data(
            rel_id,
            "remote-app",
            {"publications": json.dumps({})},
        )

        result = harness.charm.logical_replication._would_create_circular_replication(
            relation, "testdb", "public.test_table"
        )
        assert result is False


def test_would_create_circular_replication_table_not_published(harness):
    """Test circular detection when table is not in publication."""
    with (
        patch("charm.Patroni.get_primary"),
        harness.hooks_disabled(),
    ):
        rel_id = harness.add_relation("logical-replication", "remote-app")
        relation = harness.model.get_relation("logical-replication", rel_id)

        # Set publications without the table we're checking
        publications = {
            "testdb": {
                "publication-name": "test_pub",
                "replication-chains": {"public.other_table": ["remote-app"]},
            }
        }
        harness.update_relation_data(
            rel_id,
            "remote-app",
            {"publications": json.dumps(publications)},
        )

        result = harness.charm.logical_replication._would_create_circular_replication(
            relation, "testdb", "public.test_table"
        )
        assert result is False


def test_would_create_circular_replication_simple_bidirectional(harness):
    """Test circular detection for simple A <-> B case."""
    with (
        patch("charm.Patroni.get_primary"),
        harness.hooks_disabled(),
    ):
        rel_id = harness.add_relation("logical-replication", "remote-app")
        relation = harness.model.get_relation("logical-replication", rel_id)

        # Simulate that remote-app is publishing a table that originated from us
        publications = {
            "testdb": {
                "publication-name": "test_pub",
                "replication-chains": {
                    "public.test_table": [harness.charm.app.name, "remote-app"]
                },
            }
        }
        harness.update_relation_data(
            rel_id,
            "remote-app",
            {"publications": json.dumps(publications)},
        )

        # Now we try to subscribe to it - this would create a cycle
        result = harness.charm.logical_replication._would_create_circular_replication(
            relation, "testdb", "public.test_table"
        )
        assert result is True


def test_would_create_circular_replication_multihop(harness):
    """Test circular detection for A -> B -> C -> A case."""
    with (
        patch("charm.Patroni.get_primary"),
        harness.hooks_disabled(),
    ):
        rel_id = harness.add_relation("logical-replication", "cluster-c")
        relation = harness.model.get_relation("logical-replication", rel_id)

        # Simulate A -> B -> C chain
        # cluster-c is publishing a table that originated from our app (harness.charm.app.name)
        # and passed through cluster-b
        publications = {
            "testdb": {
                "publication-name": "test_pub",
                "replication-chains": {
                    "public.test_table": [harness.charm.app.name, "cluster-b", "cluster-c"]
                },
            }
        }
        harness.update_relation_data(
            rel_id,
            "cluster-c",
            {"publications": json.dumps(publications)},
        )

        # Our app tries to subscribe - this would create a cycle
        result = harness.charm.logical_replication._would_create_circular_replication(
            relation, "testdb", "public.test_table"
        )
        assert result is True


def test_would_create_circular_replication_different_table_ok(harness):
    """Test that different tables don't trigger circular detection."""
    with (
        patch("charm.Patroni.get_primary"),
        harness.hooks_disabled(),
    ):
        rel_id = harness.add_relation("logical-replication", "remote-app")
        relation = harness.model.get_relation("logical-replication", rel_id)

        # remote-app is publishing table1 that came from us
        publications = {
            "testdb": {
                "publication-name": "test_pub",
                "replication-chains": {"public.table1": [harness.charm.app.name, "remote-app"]},
            }
        }
        harness.update_relation_data(
            rel_id,
            "remote-app",
            {"publications": json.dumps(publications)},
        )

        # We try to subscribe to a different table - this is OK
        result = harness.charm.logical_replication._would_create_circular_replication(
            relation, "testdb", "public.table2"
        )
        assert result is False


def test_check_publisher_circular_replication_no_subscription(harness):
    """Test publisher check when not subscribed to anything."""
    with (
        patch("charm.Patroni.get_primary"),
        harness.hooks_disabled(),
    ):
        # Create an offer relation
        offer_rel_id = harness.add_relation("logical-replication-offer", "remote-app")
        offer_relation = harness.model.get_relation("logical-replication-offer", offer_rel_id)

        result = harness.charm.logical_replication._check_publisher_circular_replication(
            offer_relation, "testdb", ["public.test_table"]
        )
        assert result == []


def test_check_publisher_circular_replication_different_database(harness):
    """Test publisher check when subscribed to different database."""
    with (
        patch("charm.Patroni.get_primary"),
        harness.hooks_disabled(),
    ):
        # Create subscription relation
        rel_id = harness.add_relation("logical-replication", "remote-app")

        # Subscribe to a different database
        harness.update_relation_data(
            rel_id,
            "remote-app",
            {"publications": json.dumps({"otherdb": {"tables": ["public.test_table"]}})},
        )

        # Store subscription info
        harness.update_relation_data(
            harness.model.get_relation(PEER).id,
            harness.charm.app.name,
            {
                "logical-replication-subscriptions": json.dumps({
                    str(rel_id): {"otherdb": "subscription_name"}
                })
            },
        )

        # Create an offer relation from the same remote app
        offer_rel_id = harness.add_relation("logical-replication-offer", "remote-app")
        offer_relation = harness.model.get_relation("logical-replication-offer", offer_rel_id)

        result = harness.charm.logical_replication._check_publisher_circular_replication(
            offer_relation, "testdb", ["public.test_table"]
        )
        assert result == []


def test_check_publisher_circular_replication_detects_cycle(harness):
    """Test publisher detects when trying to publish a subscribed table."""
    with (
        patch("charm.Patroni.get_primary"),
        harness.hooks_disabled(),
    ):
        # Create subscription relation to remote-app
        rel_id = harness.add_relation("logical-replication", "remote-app")

        # We're subscribed to test_table from remote-app
        harness.update_relation_data(
            rel_id,
            "remote-app",
            {
                "publications": json.dumps({
                    "testdb": {"tables": ["public.test_table", "public.other_table"]}
                })
            },
        )

        # Store subscription info
        harness.update_relation_data(
            harness.model.get_relation(PEER).id,
            harness.charm.app.name,
            {
                "logical-replication-subscriptions": json.dumps({
                    str(rel_id): {"testdb": "subscription_name"}
                })
            },
        )

        # Create an offer relation from the same remote app
        offer_rel_id = harness.add_relation("logical-replication-offer", "remote-app")
        offer_relation = harness.model.get_relation("logical-replication-offer", offer_rel_id)

        # Now try to publish the same table - should detect circular
        result = harness.charm.logical_replication._check_publisher_circular_replication(
            offer_relation, "testdb", ["public.test_table", "public.another_table"]
        )
        assert result == ["public.test_table"]


def test_build_replication_chains_no_subscription(harness):
    """Test building chains when we're the origin."""
    with patch("charm.Patroni.get_primary"):
        chains = harness.charm.logical_replication._build_replication_chains(
            "testdb", ["public.table1", "public.table2"]
        )

        assert chains == {
            "public.table1": [harness.charm.app.name],
            "public.table2": [harness.charm.app.name],
        }


def test_build_replication_chains_extends_chain(harness):
    """Test building chains extends existing chains."""
    with (
        patch("charm.Patroni.get_primary"),
        harness.hooks_disabled(),
    ):
        rel_id = harness.add_relation("logical-replication", "cluster-b")

        # We're subscribed to tables from cluster-b, which got them from cluster-a
        harness.update_relation_data(
            rel_id,
            "cluster-b",
            {
                "publications": json.dumps({
                    "testdb": {
                        "replication-chains": {
                            "public.table1": ["cluster-a", "cluster-b"],
                            "public.table2": ["cluster-b"],  # cluster-b is origin for table2
                        }
                    }
                })
            },
        )

        # Build chains for publishing these tables
        chains = harness.charm.logical_replication._build_replication_chains(
            "testdb", ["public.table1", "public.table2", "public.table3"]
        )

        # table1: extends cluster-a -> cluster-b chain
        # table2: extends cluster-b chain
        # table3: we're the origin
        assert chains == {
            "public.table1": ["cluster-a", "cluster-b", harness.charm.app.name],
            "public.table2": ["cluster-b", harness.charm.app.name],
            "public.table3": [harness.charm.app.name],
        }


def test_validate_subscription_request_blocks_circular(harness):
    """Test that validation blocks circular replication."""
    with (
        patch("charm.Patroni.get_primary"),
        patch("charm.PostgresqlOperatorCharm.postgresql") as mock_pg,
        harness.hooks_disabled(),
    ):
        # Setup mocks
        mock_pg.database_exists.return_value = True
        mock_pg.table_exists.return_value = True
        mock_pg.is_table_empty.return_value = True

        # Create logical replication relation
        rel_id = harness.add_relation("logical-replication", "remote-app")

        # Remote is publishing a table that originated from us
        harness.update_relation_data(
            rel_id,
            "remote-app",
            {
                "publications": json.dumps({
                    "testdb": {
                        "replication-chains": {
                            "public.test_table": [harness.charm.app.name, "remote-app"]
                        }
                    }
                })
            },
        )

        # Set config to subscribe to the same table
        harness.update_config({
            "logical_replication_subscription_request": json.dumps({
                "testdb": ["public.test_table"]
            })
        })

        # Validation should fail due to circular replication
        result = harness.charm.logical_replication._validate_subscription_request()

        assert result is False
        assert harness.charm.app_peer_data.get("logical-replication-validation") == "error"
