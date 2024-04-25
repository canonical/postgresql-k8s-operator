# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import unittest
from unittest.mock import MagicMock, PropertyMock, call, patch

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


class TestUpgrade(unittest.TestCase):
    """Test the upgrade class."""

    @patch("charm.KubernetesServicePatch", lambda x, y: None)
    def setUp(self):
        """Set up the test."""
        self.patcher = patch("lightkube.core.client.GenericSyncClient")
        self.patcher.start()
        self.harness = Harness(PostgresqlOperatorCharm)
        self.harness.begin()
        self.upgrade_relation_id = self.harness.add_relation("upgrade", "postgresql-k8s")
        self.peer_relation_id = self.harness.add_relation("database-peers", "postgresql-k8s")
        for rel_id in (self.upgrade_relation_id, self.peer_relation_id):
            self.harness.add_relation_unit(rel_id, "postgresql-k8s/1")
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.upgrade_relation_id, "postgresql-k8s/1", {"state": "idle"}
            )
        self.charm = self.harness.charm

    def test_is_no_sync_member(self):
        # Test when there is no list of sync-standbys in the relation data.
        self.assertFalse(self.charm.upgrade.is_no_sync_member)

        # Test when the current unit is not part of the list of sync-standbys
        # from the relation data.
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.upgrade_relation_id,
                self.charm.app.name,
                {"sync-standbys": '["postgresql-k8s/1", "postgresql-k8s/2"]'},
            )
        self.assertTrue(self.charm.upgrade.is_no_sync_member)

        # Test when the current unit is part of the list of sync-standbys from the relation data.
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.upgrade_relation_id,
                self.charm.app.name,
                {
                    "sync-standbys": f'["{self.charm.unit.name}", "postgresql-k8s/1", "postgresql-k8s/2"]'
                },
            )
        self.assertFalse(self.charm.upgrade.is_no_sync_member)

    @patch("charm.PostgresqlOperatorCharm.update_config")
    @patch("upgrade.logger.info")
    def test_log_rollback(self, mock_logging, _update_config):
        self.charm.upgrade.log_rollback_instructions()
        calls = [
            call(
                "Run `juju refresh --revision <previous-revision> postgresql-k8s` to initiate the rollback"
            ),
            call(
                "and `juju run-action postgresql-k8s/leader resume-upgrade` to resume the rollback"
            ),
        ]
        mock_logging.assert_has_calls(calls)

    @patch("charms.data_platform_libs.v0.upgrade.DataUpgrade.set_unit_failed")
    @patch("charms.data_platform_libs.v0.upgrade.DataUpgrade.set_unit_completed")
    @patch("charm.Patroni.is_replication_healthy", new_callable=PropertyMock)
    @patch("charm.Patroni.cluster_members", new_callable=PropertyMock)
    @patch("upgrade.wait_fixed", return_value=tenacity.wait_fixed(0))
    @patch("charm.Patroni.member_started", new_callable=PropertyMock)
    def test_on_postgresql_pebble_ready(
        self,
        _member_started,
        _,
        _cluster_members,
        _is_replication_healthy,
        _set_unit_completed,
        _set_unit_failed,
    ):
        # Set some side effects to test multiple situations.
        _member_started.side_effect = [False, True, True, True]

        # Test when the unit status is different from "upgrading".
        mock_event = MagicMock()
        self.charm.upgrade._on_postgresql_pebble_ready(mock_event)
        _member_started.assert_not_called()
        mock_event.defer.assert_not_called()
        _set_unit_completed.assert_not_called()
        _set_unit_failed.assert_not_called()

        # Test when the unit status is equal to "upgrading", but the member hasn't started yet.
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.upgrade_relation_id, self.charm.unit.name, {"state": "upgrading"}
            )
        self.charm.upgrade._on_postgresql_pebble_ready(mock_event)
        _member_started.assert_called_once()
        mock_event.defer.assert_called_once()
        _set_unit_completed.assert_not_called()
        _set_unit_failed.assert_not_called()

        # Test when the unit status is equal to "upgrading", and the member has already started
        # but not joined the cluster yet.
        _member_started.reset_mock()
        mock_event.defer.reset_mock()
        _cluster_members.return_value = ["postgresql-k8s-1"]
        self.charm.upgrade._on_postgresql_pebble_ready(mock_event)
        _member_started.assert_called_once()
        mock_event.defer.assert_not_called()
        _set_unit_completed.assert_not_called()
        _set_unit_failed.assert_called_once()

        # Test when the member has already joined the cluster, but replication
        # is not healthy yet.
        _set_unit_failed.reset_mock()
        mock_event.defer.reset_mock()
        _cluster_members.return_value = [
            self.charm.unit.name.replace("/", "-"),
            "postgresql-k8s-1",
        ]
        _is_replication_healthy.return_value = False
        self.charm.upgrade._on_postgresql_pebble_ready(mock_event)
        mock_event.defer.assert_not_called()
        _set_unit_completed.assert_not_called()
        _set_unit_failed.assert_called_once()

        # Test when replication is healthy.
        _member_started.reset_mock()
        _set_unit_failed.reset_mock()
        mock_event.defer.reset_mock()
        _is_replication_healthy.return_value = True
        self.charm.upgrade._on_postgresql_pebble_ready(mock_event)
        _member_started.assert_called_once()
        mock_event.defer.assert_not_called()
        _set_unit_completed.assert_called_once()
        _set_unit_failed.assert_not_called()

    @patch("charm.PostgresqlOperatorCharm.update_config")
    @patch("charm.Patroni.member_started", new_callable=PropertyMock)
    def test_on_upgrade_changed(self, _member_started, _update_config):
        _member_started.return_value = False
        relation = self.harness.model.get_relation("upgrade")
        self.charm.on.upgrade_relation_changed.emit(relation)
        _update_config.assert_not_called()

        _member_started.return_value = True
        self.charm.on.upgrade_relation_changed.emit(relation)
        _update_config.assert_called_once()

    @patch("charm.PostgreSQLUpgrade._set_rolling_update_partition")
    @patch("charm.PostgreSQLUpgrade._set_list_of_sync_standbys")
    @patch("charm.Patroni.switchover")
    @patch("charm.Patroni.get_sync_standby_names")
    @patch("charm.PostgresqlOperatorCharm.update_config")
    @patch("charm.Patroni.get_primary")
    @patch("charm.Patroni.is_creating_backup", new_callable=PropertyMock)
    @patch("charm.Patroni.are_all_members_ready")
    def test_pre_upgrade_check(
        self,
        _are_all_members_ready,
        _is_creating_backup,
        _get_primary,
        _update_config,
        _get_sync_standby_names,
        _switchover,
        _set_list_of_sync_standbys,
        _set_rolling_update_partition,
    ):
        self.harness.set_leader(True)

        # Set some side effects to test multiple situations.
        _are_all_members_ready.side_effect = [False, True, True, True, True, True, True]
        _is_creating_backup.side_effect = [True, False, False, False, False, False]
        _switchover.side_effect = [None, SwitchoverFailedError]

        # Test when not all members are ready.
        with self.assertRaises(ClusterNotReadyError):
            self.charm.upgrade.pre_upgrade_check()
        _switchover.assert_not_called()
        _set_list_of_sync_standbys.assert_not_called()
        _set_rolling_update_partition.assert_not_called()

        # Test when a backup is being created.
        with self.assertRaises(ClusterNotReadyError):
            self.charm.upgrade.pre_upgrade_check()
        _switchover.assert_not_called()
        _set_list_of_sync_standbys.assert_not_called()
        _set_rolling_update_partition.assert_not_called()

        # Test when the primary is already the first unit.
        unit_zero_name = f"{self.charm.app.name}/0"
        _get_primary.return_value = unit_zero_name
        self.charm.upgrade.pre_upgrade_check()
        _switchover.assert_not_called()
        _set_list_of_sync_standbys.assert_not_called()
        _set_rolling_update_partition.assert_called_once_with(self.charm.app.planned_units() - 1)

        # Test when there are no sync-standbys.
        _set_rolling_update_partition.reset_mock()
        _get_primary.return_value = f"{self.charm.app.name}/1"
        _get_sync_standby_names.return_value = []
        with self.assertRaises(ClusterNotReadyError):
            self.charm.upgrade.pre_upgrade_check()
        _switchover.assert_not_called()
        _set_list_of_sync_standbys.assert_not_called()
        _set_rolling_update_partition.assert_not_called()

        # Test when the first unit is a sync-standby.
        _set_rolling_update_partition.reset_mock()
        _get_sync_standby_names.return_value = [unit_zero_name, f"{self.charm.app.name}/2"]
        self.charm.upgrade.pre_upgrade_check()
        _switchover.assert_called_once_with(unit_zero_name)
        _set_list_of_sync_standbys.assert_not_called()
        _set_rolling_update_partition.assert_called_once_with(self.charm.app.planned_units() - 1)

        # Test when the switchover fails.
        _switchover.reset_mock()
        _set_rolling_update_partition.reset_mock()
        with self.assertRaises(ClusterNotReadyError):
            self.charm.upgrade.pre_upgrade_check()
        _switchover.assert_called_once_with(unit_zero_name)
        _set_list_of_sync_standbys.assert_not_called()
        _set_rolling_update_partition.assert_not_called()

        # Test when the first unit is neither the primary nor a sync-standby.
        _switchover.reset_mock()
        _set_rolling_update_partition.reset_mock()
        _get_sync_standby_names.return_value = f'["{self.charm.app.name}/2"]'
        with self.assertRaises(ClusterNotReadyError):
            self.charm.upgrade.pre_upgrade_check()
        _switchover.assert_not_called()
        _set_list_of_sync_standbys.assert_called_once()
        _set_rolling_update_partition.assert_not_called()

    @patch("charm.Patroni.get_sync_standby_names")
    def test_set_list_of_sync_standbys(self, _get_sync_standby_names):
        # Mock some return values.
        _get_sync_standby_names.side_effect = [
            ["postgresql-k8s/1"],
            ["postgresql-k8s/0", "postgresql-k8s/1"],
            ["postgresql-k8s/1", "postgresql-k8s/2"],
        ]

        # Test when the there are less than 3 units in the cluster.
        self.charm.upgrade._set_list_of_sync_standbys()
        self.assertNotIn(
            "sync-standbys",
            self.harness.get_relation_data(self.upgrade_relation_id, self.charm.app),
        )

        # Test when the there are 3 units in the cluster.
        for rel_id in (self.upgrade_relation_id, self.peer_relation_id):
            self.harness.add_relation_unit(rel_id, "postgresql-k8s/2")
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.upgrade_relation_id, "postgresql-k8s/2", {"state": "idle"}
            )
        self.charm.upgrade._set_list_of_sync_standbys()
        self.assertEqual(
            self.harness.get_relation_data(self.upgrade_relation_id, self.charm.app)[
                "sync-standbys"
            ],
            '["postgresql-k8s/0"]',
        )

        # Test when the unit zero is already a sync-standby.
        for rel_id in (self.upgrade_relation_id, self.peer_relation_id):
            self.harness.add_relation_unit(rel_id, "postgresql-k8s/3")
        with self.harness.hooks_disabled():
            self.harness.update_relation_data(
                self.upgrade_relation_id, "postgresql-k8s/3", {"state": "idle"}
            )
        self.charm.upgrade._set_list_of_sync_standbys()
        self.assertEqual(
            self.harness.get_relation_data(self.upgrade_relation_id, self.charm.app)[
                "sync-standbys"
            ],
            '["postgresql-k8s/0", "postgresql-k8s/1"]',
        )

        # Test when the unit zero is not a sync-standby yet.
        self.charm.upgrade._set_list_of_sync_standbys()
        self.assertEqual(
            self.harness.get_relation_data(self.upgrade_relation_id, self.charm.app)[
                "sync-standbys"
            ],
            '["postgresql-k8s/1", "postgresql-k8s/0"]',
        )

    @patch("upgrade.Client")
    def test_set_rolling_update_partition(self, _client):
        # Test the successful operation.
        self.charm.upgrade._set_rolling_update_partition(2)
        _client.return_value.patch.assert_called_once_with(
            StatefulSet,
            name=self.charm.app.name,
            namespace=self.charm.model.name,
            obj={"spec": {"updateStrategy": {"rollingUpdate": {"partition": 2}}}},
        )

        # Test an operation that failed due to lack of Juju's trust flag.
        _client.return_value.patch.reset_mock()
        _client.return_value.patch.side_effect = _FakeApiError(403)
        with self.assertRaises(KubernetesClientError) as exception:
            self.charm.upgrade._set_rolling_update_partition(2)
        self.assertEqual(exception.exception.cause, "`juju trust` needed")

        # Test an operation that failed due to some other reason.
        _client.return_value.patch.reset_mock()
        _client.return_value.patch.side_effect = _FakeApiError
        with self.assertRaises(KubernetesClientError) as exception:
            self.charm.upgrade._set_rolling_update_partition(2)
        self.assertEqual(exception.exception.cause, "broken")
