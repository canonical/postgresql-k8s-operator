# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import patch

import pytest
from ops.testing import Harness

from charm import PostgresqlOperatorCharm
from constants import PEER

RELATION_NAMES = ["replication-offer", "replication"]


@pytest.fixture(autouse=True)
def harness():
    with patch("charm.KubernetesServicePatch", lambda x, y: None):
        harness = Harness(PostgresqlOperatorCharm)

        # Set up the initial relation and hooks.
        harness.set_leader(True)
        harness.begin()

        yield harness
        harness.cleanup()


@pytest.mark.parametrize("relation_name", RELATION_NAMES)
@pytest.mark.parametrize("is_leader", [True, False])
def test_on_async_relation_broken(harness, is_leader, relation_name):
    with (
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch(
            "relations.async_replication.PostgreSQLAsyncReplication._set_app_status"
        ) as _set_app_status,
        patch("charm.Patroni.get_standby_leader") as _get_standby_leader,
        patch(
            "relations.async_replication.PostgreSQLAsyncReplication._on_async_relation_departed"
        ) as _on_async_relation_departed,
    ):
        # Test before the peer relation is available.
        with harness.hooks_disabled():
            harness.set_leader(is_leader)
            rel_id = harness.add_relation(relation_name, harness.charm.app.name)
            harness.add_relation_unit(rel_id, harness.charm.unit.name)
        harness.remove_relation(rel_id)
        _get_standby_leader.assert_not_called()
        _set_app_status.assert_not_called()

        # Test the departing unit.
        with harness.hooks_disabled():
            peer_rel_id = harness.add_relation(PEER, harness.charm.app.name)
            harness.update_relation_data(
                peer_rel_id,
                harness.charm.app.name,
                {"promoted-cluster-counter": "1"},
            )
            harness.update_relation_data(
                peer_rel_id,
                harness.charm.unit.name,
                {"departing": "True", "stopped": "True", "unit-promoted-cluster-counter": "1"},
            )
            rel_id = harness.add_relation(relation_name, harness.charm.app.name)
            harness.add_relation_unit(rel_id, harness.charm.unit.name)
        harness.remove_relation(rel_id)
        # assert harness.get_relation_data(peer_rel_id, harness.charm.app.name) == {
        #     "promoted-cluster-counter": ("0" if is_leader else "0")}
        assert harness.get_relation_data(peer_rel_id, harness.charm.unit.name) == {
            "departing": "True",
            "stopped": "True",
            "unit-promoted-cluster-counter": "1",
        }
        _get_standby_leader.assert_not_called()
        _set_app_status.assert_not_called()

        # Test in a primary cluster.
        with harness.hooks_disabled():
            _get_standby_leader.return_value = None
            harness.update_relation_data(
                peer_rel_id,
                harness.charm.unit.name,
                {"departing": "", "stopped": "True", "unit-promoted-cluster-counter": "1"},
            )
            rel_id = harness.add_relation(relation_name, harness.charm.app.name)
            harness.add_relation_unit(rel_id, harness.charm.unit.name)
        harness.remove_relation(rel_id)
        assert harness.get_relation_data(peer_rel_id, harness.charm.app.name) == (
            {} if is_leader else {"promoted-cluster-counter": "1"}
        )
        assert harness.get_relation_data(peer_rel_id, harness.charm.unit.name) == {}
        _get_standby_leader.assert_called_once()
        _update_config.assert_called_once()

        # Test in a standby cluster.
        _update_config.reset_mock()
        with harness.hooks_disabled():
            _get_standby_leader.return_value = harness.charm.unit.name
            harness.update_relation_data(
                peer_rel_id,
                harness.charm.unit.name,
                {"stopped": "True", "unit-promoted-cluster-counter": "1"},
            )
            rel_id = harness.add_relation(relation_name, harness.charm.app.name)
            harness.add_relation_unit(rel_id, harness.charm.unit.name)
        harness.remove_relation(rel_id)
        assert harness.get_relation_data(peer_rel_id, harness.charm.app.name) == {
            "promoted-cluster-counter": ("0" if is_leader else "1")
        }
        assert harness.get_relation_data(peer_rel_id, harness.charm.unit.name) == {}
        assert _set_app_status.call_count == (1 if is_leader else 0)
        _update_config.assert_not_called()


@pytest.mark.parametrize("relation_name", RELATION_NAMES)
def test_on_async_relation_created(harness, relation_name):
    with (
        patch(
            "relations.async_replication.PostgreSQLAsyncReplication._get_highest_promoted_cluster_counter_value",
            side_effect=["0", "1"],
        ) as _get_highest_promoted_cluster_counter_value,
        patch(
            "relations.async_replication.PostgreSQLAsyncReplication._get_unit_ip",
            return_value="1.1.1.1",
        ) as _get_unit_ip,
    ):
        # Test in a standby cluster.
        with harness.hooks_disabled():
            peer_rel_id = harness.add_relation(PEER, harness.charm.app.name)
        rel_id = harness.add_relation(relation_name, harness.charm.app.name)
        assert harness.get_relation_data(rel_id, harness.charm.unit.name) == {
            "unit-address": "1.1.1.1"
        }
        assert harness.get_relation_data(peer_rel_id, harness.charm.unit.name) == {}

        # Test in a primary cluster.
        with harness.hooks_disabled():
            harness.update_relation_data(rel_id, harness.charm.unit.name, {"unit-address": ""})
            harness.remove_relation(rel_id)
        rel_id = harness.add_relation(relation_name, harness.charm.app.name)
        assert harness.get_relation_data(rel_id, harness.charm.unit.name) == {
            "unit-address": "1.1.1.1"
        }
        assert harness.get_relation_data(peer_rel_id, harness.charm.unit.name) == {
            "unit-promoted-cluster-counter": "1"
        }


@pytest.mark.parametrize("relation_name", RELATION_NAMES)
def test_on_async_relation_departed(harness, relation_name):
    # Test the departing unit.
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, harness.charm.app.name)
        rel_id = harness.add_relation(relation_name, harness.charm.app.name)
        harness.add_relation_unit(rel_id, harness.charm.unit.name)
    harness.remove_relation_unit(rel_id, harness.charm.unit.name)
    assert harness.get_relation_data(peer_rel_id, harness.charm.unit.name) == {"departing": "True"}

    # Test the non-departing unit.
    other_unit = f"{harness.charm.app.name}/1"
    with harness.hooks_disabled():
        harness.update_relation_data(peer_rel_id, harness.charm.unit.name, {"departing": ""})
        harness.add_relation_unit(rel_id, other_unit)
    harness.remove_relation_unit(rel_id, other_unit)
    assert harness.get_relation_data(peer_rel_id, harness.charm.unit.name) == {}
