# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import json
from unittest.mock import PropertyMock, patch

import pytest
from ops.testing import Harness

from charm import PostgresqlOperatorCharm
from constants import APP_SCOPE, PEER
from relations.async_replication import (
    REPLICATION_CONSUMER_RELATION,
    REPLICATION_OFFER_RELATION,
    SECRET_LABEL,
)

RELATION_NAMES = ["replication-offer", "replication"]


@pytest.fixture(autouse=True)
def harness():
    harness = Harness(PostgresqlOperatorCharm)

    # Set up the initial relation and hooks.
    harness.set_leader(True)
    harness.begin()

    yield harness
    harness.cleanup()


@pytest.fixture(autouse=True)
def standby():
    harness = Harness(PostgresqlOperatorCharm)
    harness.set_model_name("standby")

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


@pytest.mark.parametrize("wait_for_standby", [True, False])
def test_on_async_relation_changed(harness, wait_for_standby):
    with patch(
        "relations.async_replication.PostgreSQLAsyncReplication._get_unit_ip",
        return_value="1.1.1.1",
    ) as _get_unit_ip:
        harness.add_relation(
            PEER,
            harness.charm.app.name,
            unit_data={"unit-address": "10.1.1.10"},
            app_data={"promoted-cluster-counter": "1"},
        )
        harness.set_can_connect("postgresql", True)
        harness.handle_exec("postgresql", [], result=0)
        harness.add_relation(REPLICATION_OFFER_RELATION, harness.charm.app.name)
        assert harness.charm.async_replication.get_primary_cluster().name == harness.charm.app.name

    with (
        patch("ops.model.Container.stop") as _stop,
        patch("ops.model.Container.start") as _start,
        patch("ops.model.Container.pebble") as _pebble,
        patch("lightkube.Client.__init__", return_value=None),
        patch("lightkube.Client.delete") as _lightkube_delete,
        patch(
            "charm.Patroni.member_started", new_callable=PropertyMock
        ) as _patroni_member_started,
        patch("charm.PostgresqlOperatorCharm._create_pgdata") as _create_pgdata,
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.PostgresqlOperatorCharm._set_active_status") as _set_active_status,
        patch(
            "relations.async_replication.PostgreSQLAsyncReplication.get_system_identifier",
            return_value=("12345", None),
        ),
        patch(
            "relations.async_replication.PostgreSQLAsyncReplication._wait_for_standby_leader",
            return_value=wait_for_standby,
        ),
        patch(
            "relations.async_replication.PostgreSQLAsyncReplication._get_unit_ip",
            return_value="1.1.1.1",
        ) as _get_unit_ip,
    ):
        _pebble.get_services.return_value = ["postgresql"]
        _patroni_member_started.return_value = True
        harness.add_relation(
            REPLICATION_CONSUMER_RELATION,
            "standby",
            unit_data={"unit-address": "10.2.2.10"},
            app_data={"promoted-cluster-counter": "2"},
        )
        _stop.assert_called_once()
        if not wait_for_standby:
            _start.assert_called()
        _create_pgdata.assert_called_once()

    assert harness.charm.async_replication.get_primary_cluster().name == "standby"


@pytest.mark.parametrize("relation_name", [REPLICATION_OFFER_RELATION])
def test_create_replication(harness, relation_name):
    """Test create-replication action."""
    with (
        patch(
            "charm.PostgresqlOperatorCharm.is_cluster_initialised",
            new_callable=PropertyMock,
            return_value=False,
        ) as _is_cluster_initialised,
        patch(
            "relations.async_replication.PostgreSQLAsyncReplication._get_unit_ip",
            return_value="10.1.1.10",
        ),
        patch(
            "relations.async_replication.PostgreSQLAsyncReplication.get_system_identifier",
            return_value=("12345", None),
        ),
        patch(
            "relations.async_replication.PostgreSQLAsyncReplication._primary_cluster_endpoint",
            new_callable=PropertyMock,
            return_value="10.1.1.10",
        ),
        patch("charm.Patroni.get_standby_leader", return_value=None),
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.PostgresqlOperatorCharm._set_active_status") as _set_active_status,
    ):
        harness.charm.model.app.add_secret(
            {"password": "password"}, label="database-peers.postgresql-k8s.app"
        )
        with harness.hooks_disabled():
            harness.add_relation(PEER, harness.charm.app.name)
        rel_id = harness.add_relation(
            relation_name, harness.charm.app.name, unit_data={"unit-address": "10.1.1.10"}
        )
        _is_cluster_initialised.return_value = True
        harness.run_action("create-replication")

        _update_config.assert_called_once()
        _set_active_status.assert_called()
        assert harness.get_relation_data(rel_id, harness.charm.app.name).get("name") == "default"


@pytest.mark.parametrize("relation_name", [REPLICATION_CONSUMER_RELATION])
def test_promote_to_primary(harness, relation_name):
    """Test promote-to-primary action."""
    with (
        patch(
            "charm.PostgresqlOperatorCharm.is_cluster_initialised",
            new_callable=PropertyMock,
            return_value=True,
        ),
        patch(
            "relations.async_replication.PostgreSQLAsyncReplication.get_system_identifier",
            return_value=("12345", None),
        ),
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.PostgresqlOperatorCharm._set_active_status") as _set_active_status,
        patch(
            "relations.async_replication.PostgreSQLAsyncReplication._primary_cluster_endpoint",
            new_callable=PropertyMock,
            return_value="10.1.1.10",
        ),
        patch("charm.Patroni.get_primary"),
        patch("charm.Patroni.get_standby_leader", return_value=None),
    ):
        with harness.hooks_disabled():
            harness.add_relation(
                PEER, harness.charm.app.name, unit_data={"unit-address": "10.1.1.10"}
            )
            rel_id = harness.add_relation(
                relation_name, "standby", app_data={"promoted-cluster-counter": "1"}
            )
            harness.update_relation_data(rel_id, "standby/0", {"unit-address": "10.2.2.10"})

        harness.run_action("promote-to-primary", {"scope": "cluster"})

        assert (
            harness.get_relation_data(rel_id, harness.charm.app.name).get(
                "promoted-cluster-counter"
            )
            == "2"
        )


@pytest.mark.parametrize("relation_name", RELATION_NAMES)
@pytest.mark.skip(reason="Skipping to run integration tests on CI")
def test_on_secret_changed(harness, relation_name):
    with patch(
        "relations.async_replication.PostgreSQLAsyncReplication._get_unit_ip",
        return_value="1.1.1.1",
    ) as _get_unit_ip:
        secret_id = harness.add_model_secret("primary", {"operator-password": "old"})
        peer_rel_id = harness.add_relation(PEER, "primary")
        rel_id = harness.add_relation(
            relation_name, harness.charm.app.name, unit_data={"unit-address": "10.1.1.10"}
        )

    secret_label = (
        f"{PEER}.{harness.charm.app.name}.app"
        if relation_name == REPLICATION_OFFER_RELATION
        else SECRET_LABEL
    )
    harness.grant_secret(secret_id, harness.charm.app.name)
    harness.charm.model.get_secret(id=secret_id, label=secret_label)
    primary_cluster_data = {
        "endpoint": "10.1.1.10",
        "secret-id": "",
        "name": "default",
    }

    with harness.hooks_disabled():
        harness.update_relation_data(
            peer_rel_id, harness.charm.unit.name, {"unit-promoted-cluster-counter": "1"}
        )
        harness.update_relation_data(
            peer_rel_id, harness.charm.app.name, {"promoted-cluster-counter": "1"}
        )
        harness.update_relation_data(
            rel_id,
            harness.charm.app.name,
            {
                "promoted-cluster-counter": "1",
                "primary-cluster-data": json.dumps(primary_cluster_data),
            },
        )

    with (
        patch(
            "charm.PostgresqlOperatorCharm._on_peer_relation_changed", return_value=None
        ) as _charm_on_peer_relation_changed,
        patch("charm.PostgresqlOperatorCharm._on_secret_changed", return_value=None),
        patch(
            "relations.async_replication.PostgreSQLAsyncReplication._primary_cluster_endpoint",
            new_callable=PropertyMock,
            return_value="10.1.1.10",
        ),
    ):
        harness.set_secret_content(secret_id, {"operator-password": "new"})

    _charm_on_peer_relation_changed.assert_called_once()
    if relation_name == REPLICATION_CONSUMER_RELATION:
        assert harness.charm.get_secret(APP_SCOPE, "operator-password") == "new"
    else:
        updated_cluster_data = json.loads(
            harness.get_relation_data(rel_id, harness.charm.app.name).get("primary-cluster-data")
        )
        assert primary_cluster_data.get("secret-id") != updated_cluster_data.get("secret-id")
