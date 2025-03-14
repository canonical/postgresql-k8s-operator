# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
from unittest.mock import MagicMock, PropertyMock, call, patch

import pytest
import tenacity
from charms.data_platform_libs.v0.upgrade import (
    ClusterNotReadyError,
    KubernetesClientError,
)
from lightkube.resources.apps_v1 import StatefulSet
from ops.testing import Harness

from charm import PostgresqlOperatorCharm
from patroni import SwitchoverFailedError
from tests.unit.helpers import _FakeApiError

POSTGRESQL_CONTAINER = "postgresql"


@pytest.fixture(autouse=True)
def harness():
    """Set up the test."""
    patcher = patch("lightkube.core.client.GenericSyncClient")
    patcher.start()
    harness = Harness(PostgresqlOperatorCharm)
    harness.begin()
    upgrade_relation_id = harness.add_relation("upgrade", "postgresql-k8s")
    peer_relation_id = harness.add_relation("database-peers", "postgresql-k8s")
    for rel_id in (upgrade_relation_id, peer_relation_id):
        harness.add_relation_unit(rel_id, "postgresql-k8s/1")
    harness.add_relation("restart", harness.charm.app.name)
    with harness.hooks_disabled():
        harness.update_relation_data(upgrade_relation_id, "postgresql-k8s/1", {"state": "idle"})
    yield harness
    harness.cleanup()


def test_is_no_sync_member(harness):
    # Test when there is no list of sync-standbys in the relation data.
    assert not harness.charm.upgrade.is_no_sync_member
    upgrade_relation_id = harness.model.get_relation("upgrade").id

    # Test when the current unit is not part of the list of sync-standbys
    # from the relation data.
    with harness.hooks_disabled():
        harness.update_relation_data(
            upgrade_relation_id,
            harness.charm.app.name,
            {"sync-standbys": '["postgresql-k8s/1", "postgresql-k8s/2"]'},
        )
    assert harness.charm.upgrade.is_no_sync_member

    # Test when the current unit is part of the list of sync-standbys from the relation data.
    with harness.hooks_disabled():
        harness.update_relation_data(
            upgrade_relation_id,
            harness.charm.app.name,
            {
                "sync-standbys": f'["{harness.charm.unit.name}", "postgresql-k8s/1", "postgresql-k8s/2"]'
            },
        )
    assert not harness.charm.upgrade.is_no_sync_member


def test_log_rollback(harness):
    with (
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("upgrade.logger.info") as mock_logging,
    ):
        harness.charm.upgrade.log_rollback_instructions()
        calls = [
            call(
                "Run `juju refresh --revision <previous-revision> postgresql-k8s` to initiate the rollback"
            ),
            call(
                "and `juju run-action postgresql-k8s/leader resume-upgrade` to resume the rollback"
            ),
        ]
        mock_logging.assert_has_calls(calls)


def test_on_postgresql_pebble_ready(harness):
    with (
        patch("charm.PostgreSQLUpgrade.set_unit_failed") as _set_unit_failed,
        patch("charm.PostgreSQLUpgrade.set_unit_completed") as _set_unit_completed,
        patch(
            "charm.Patroni.is_replication_healthy", new_callable=PropertyMock
        ) as _is_replication_healthy,
        patch("charm.Patroni.cluster_members", new_callable=PropertyMock) as _cluster_members,
        patch("upgrade.wait_fixed", return_value=tenacity.wait_fixed(0)),
        patch("charm.Patroni.member_started", new_callable=PropertyMock) as _member_started,
    ):
        # Set some side effects to test multiple situations.
        _member_started.side_effect = [False, True, True, True]
        upgrade_relation_id = harness.model.get_relation("upgrade").id

        # Test when the unit status is different from "upgrading".
        mock_event = MagicMock()
        harness.charm.upgrade._on_postgresql_pebble_ready(mock_event)
        _member_started.assert_not_called()
        mock_event.defer.assert_not_called()
        _set_unit_completed.assert_not_called()
        _set_unit_failed.assert_not_called()

        # Test when the unit status is equal to "upgrading", but the member hasn't started yet.
        with harness.hooks_disabled():
            harness.update_relation_data(
                upgrade_relation_id, harness.charm.unit.name, {"state": "upgrading"}
            )
        harness.charm.upgrade._on_postgresql_pebble_ready(mock_event)
        _member_started.assert_called_once()
        mock_event.defer.assert_called_once()
        _set_unit_completed.assert_not_called()
        _set_unit_failed.assert_not_called()

        # Test when the unit status is equal to "upgrading", and the member has already started
        # but not joined the cluster yet.
        _member_started.reset_mock()
        mock_event.defer.reset_mock()
        _cluster_members.return_value = ["postgresql-k8s-1"]
        harness.charm.upgrade._on_postgresql_pebble_ready(mock_event)
        _member_started.assert_called_once()
        mock_event.defer.assert_not_called()
        _set_unit_completed.assert_not_called()
        _set_unit_failed.assert_called_once()

        # Test when the member has already joined the cluster, but replication
        # is not healthy yet.
        _set_unit_failed.reset_mock()
        mock_event.defer.reset_mock()
        _cluster_members.return_value = [
            harness.charm.unit.name.replace("/", "-"),
            "postgresql-k8s-1",
        ]
        _is_replication_healthy.return_value = False
        harness.charm.upgrade._on_postgresql_pebble_ready(mock_event)
        mock_event.defer.assert_not_called()
        _set_unit_completed.assert_not_called()
        _set_unit_failed.assert_called_once()

        # Test when replication is healthy.
        _member_started.reset_mock()
        _set_unit_failed.reset_mock()
        mock_event.defer.reset_mock()
        _is_replication_healthy.return_value = True
        harness.charm.upgrade._on_postgresql_pebble_ready(mock_event)
        _member_started.assert_called_once()
        mock_event.defer.assert_not_called()
        _set_unit_completed.assert_called_once()
        _set_unit_failed.assert_not_called()


def test_on_upgrade_changed(harness):
    with (
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.Patroni.member_started", new_callable=PropertyMock) as _member_started,
        patch(
            "charm.PostgresqlOperatorCharm.updated_synchronous_node_count"
        ) as _updated_synchronous_node_count,
    ):
        harness.set_can_connect(POSTGRESQL_CONTAINER, True)
        _member_started.return_value = False
        relation = harness.model.get_relation("upgrade")
        harness.charm.on.upgrade_relation_changed.emit(relation)
        _update_config.assert_not_called()

        _member_started.return_value = True
        harness.charm.on.upgrade_relation_changed.emit(relation)
        _update_config.assert_called_once()
        _updated_synchronous_node_count.assert_called_once_with()


def test_pre_upgrade_check(harness):
    with (
        patch(
            "charm.PostgreSQLUpgrade._set_rolling_update_partition"
        ) as _set_rolling_update_partition,
        patch("charm.PostgreSQLUpgrade._set_list_of_sync_standbys") as _set_list_of_sync_standbys,
        patch("charm.Patroni.switchover") as _switchover,
        patch("charm.Patroni.get_sync_standby_names") as _get_sync_standby_names,
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.Patroni.get_primary") as _get_primary,
        patch(
            "charm.Patroni.is_creating_backup", new_callable=PropertyMock
        ) as _is_creating_backup,
        patch("charm.Patroni.are_all_members_ready") as _are_all_members_ready,
    ):
        harness.set_leader(True)

        # Set some side effects to test multiple situations.
        _are_all_members_ready.side_effect = [False, True, True, True, True, True, True]
        _is_creating_backup.side_effect = [True, False, False, False, False, False]
        _switchover.side_effect = [None, SwitchoverFailedError]

        # Test when not all members are ready.
        try:
            harness.charm.upgrade.pre_upgrade_check()
            assert False
        except ClusterNotReadyError:
            pass
        _switchover.assert_not_called()
        _set_list_of_sync_standbys.assert_not_called()
        _set_rolling_update_partition.assert_not_called()

        # Test when a backup is being created.
        try:
            harness.charm.upgrade.pre_upgrade_check()
            assert False
        except ClusterNotReadyError:
            pass
        _switchover.assert_not_called()
        _set_list_of_sync_standbys.assert_not_called()
        _set_rolling_update_partition.assert_not_called()

        # Test when the primary is already the first unit.
        unit_zero_name = f"{harness.charm.app.name}/0"
        _get_primary.return_value = unit_zero_name
        harness.charm.upgrade.pre_upgrade_check()
        _switchover.assert_not_called()
        _set_list_of_sync_standbys.assert_not_called()
        _set_rolling_update_partition.assert_called_once_with(
            harness.charm.app.planned_units() - 1
        )

        # Test when there are no sync-standbys.
        _set_rolling_update_partition.reset_mock()
        _get_primary.return_value = f"{harness.charm.app.name}/1"
        _get_sync_standby_names.return_value = []
        try:
            harness.charm.upgrade.pre_upgrade_check()
            assert False
        except ClusterNotReadyError:
            pass
        _switchover.assert_not_called()
        _set_list_of_sync_standbys.assert_not_called()
        _set_rolling_update_partition.assert_not_called()

        # Test when the first unit is a sync-standby.
        _set_rolling_update_partition.reset_mock()
        _get_sync_standby_names.return_value = [unit_zero_name, f"{harness.charm.app.name}/2"]
        harness.charm.upgrade.pre_upgrade_check()
        _switchover.assert_called_once_with(unit_zero_name)
        _set_list_of_sync_standbys.assert_not_called()
        _set_rolling_update_partition.assert_called_once_with(
            harness.charm.app.planned_units() - 1
        )

        # Test when the switchover fails.
        _switchover.reset_mock()
        _set_rolling_update_partition.reset_mock()
        try:
            harness.charm.upgrade.pre_upgrade_check()
            assert False
        except ClusterNotReadyError:
            pass
        _switchover.assert_called_once_with(unit_zero_name)
        _set_list_of_sync_standbys.assert_not_called()
        _set_rolling_update_partition.assert_not_called()

        # Test when the first unit is neither the primary nor a sync-standby.
        _switchover.reset_mock()
        _set_rolling_update_partition.reset_mock()
        _get_sync_standby_names.return_value = f'["{harness.charm.app.name}/2"]'
        try:
            harness.charm.upgrade.pre_upgrade_check()
            assert False
        except ClusterNotReadyError:
            pass
        _switchover.assert_not_called()
        _set_list_of_sync_standbys.assert_called_once()
        _set_rolling_update_partition.assert_not_called()


def test_set_list_of_sync_standbys(harness):
    with patch("charm.Patroni.get_sync_standby_names") as _get_sync_standby_names:
        upgrade_relation_id = harness.model.get_relation("upgrade").id
        peer_relation_id = harness.model.get_relation("database-peers").id
        # Mock some return values.
        _get_sync_standby_names.side_effect = [
            ["postgresql-k8s/1"],
            ["postgresql-k8s/0", "postgresql-k8s/1"],
            ["postgresql-k8s/1", "postgresql-k8s/2"],
        ]

        # Test when the there are less than 3 units in the cluster.
        harness.charm.upgrade._set_list_of_sync_standbys()
        assert "sync-standbys" not in harness.get_relation_data(
            upgrade_relation_id, harness.charm.app
        )

        # Test when the there are 3 units in the cluster.
        for rel_id in (upgrade_relation_id, peer_relation_id):
            harness.add_relation_unit(rel_id, "postgresql-k8s/2")
        with harness.hooks_disabled():
            harness.update_relation_data(
                upgrade_relation_id, "postgresql-k8s/2", {"state": "idle"}
            )
        harness.charm.upgrade._set_list_of_sync_standbys()
        assert (
            harness.get_relation_data(upgrade_relation_id, harness.charm.app)["sync-standbys"]
            == '["postgresql-k8s/0"]'
        )

        # Test when the unit zero is already a sync-standby.
        for rel_id in (upgrade_relation_id, peer_relation_id):
            harness.add_relation_unit(rel_id, "postgresql-k8s/3")
        with harness.hooks_disabled():
            harness.update_relation_data(
                upgrade_relation_id, "postgresql-k8s/3", {"state": "idle"}
            )
        harness.charm.upgrade._set_list_of_sync_standbys()
        assert (
            harness.get_relation_data(upgrade_relation_id, harness.charm.app)["sync-standbys"]
            == '["postgresql-k8s/0", "postgresql-k8s/1"]'
        )

        # Test when the unit zero is not a sync-standby yet.
        harness.charm.upgrade._set_list_of_sync_standbys()
        assert (
            harness.get_relation_data(upgrade_relation_id, harness.charm.app)["sync-standbys"]
            == '["postgresql-k8s/1", "postgresql-k8s/0"]'
        )


def test_set_rolling_update_partition(harness):
    with patch("upgrade.Client") as _client:
        # Test the successful operation.
        harness.charm.upgrade._set_rolling_update_partition(2)
        _client.return_value.patch.assert_called_once_with(
            StatefulSet,
            name=harness.charm.app.name,
            namespace=harness.charm.model.name,
            obj={"spec": {"updateStrategy": {"rollingUpdate": {"partition": 2}}}},
        )

        # Test an operation that failed due to lack of Juju's trust flag.
        _client.return_value.patch.reset_mock()
        _client.return_value.patch.side_effect = _FakeApiError(403)
        try:
            harness.charm.upgrade._set_rolling_update_partition(2)
            assert False
        except KubernetesClientError as exception:
            assert exception.cause == "`juju trust` needed"

        # Test an operation that failed due to some other reason.
        _client.return_value.patch.reset_mock()
        _client.return_value.patch.side_effect = _FakeApiError
        try:
            harness.charm.upgrade._set_rolling_update_partition(2)
            assert False
        except KubernetesClientError as exception:
            assert exception.cause == "broken"
