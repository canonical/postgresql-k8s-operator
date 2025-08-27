# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Upgrades implementation."""

import json
import logging
from typing import override

from charms.data_platform_libs.v0.upgrade import (
    ClusterNotReadyError,
    DataUpgrade,
    DependencyModel,
    KubernetesClientError,
)
from charms.postgresql_k8s.v0.postgresql import ACCESS_GROUPS
from lightkube.core.client import Client
from lightkube.core.exceptions import ApiError
from lightkube.resources.apps_v1 import StatefulSet
from ops.charm import UpgradeCharmEvent, WorkloadEvent
from ops.model import BlockedStatus, MaintenanceStatus, RelationDataContent
from pydantic import BaseModel
from tenacity import RetryError, Retrying, stop_after_attempt, wait_fixed

from constants import APP_SCOPE, MONITORING_PASSWORD_KEY, MONITORING_USER, PATRONI_PASSWORD_KEY
from patroni import SwitchoverFailedError
from utils import new_password

logger = logging.getLogger(__name__)


class PostgreSQLDependencyModel(BaseModel):
    """PostgreSQL dependencies model."""

    charm: DependencyModel
    rock: DependencyModel


def get_postgresql_k8s_dependencies_model() -> PostgreSQLDependencyModel:
    """Return the PostgreSQL dependencies model."""
    with open("src/dependency.json") as dependency_file:
        _deps = json.load(dependency_file)
    return PostgreSQLDependencyModel(**_deps)


class PostgreSQLUpgrade(DataUpgrade):
    """PostgreSQL upgrade class."""

    def __init__(self, charm, model: BaseModel, **kwargs) -> None:
        """Initialize the class."""
        super().__init__(charm, model, **kwargs)
        self.charm = charm

        self.framework.observe(self.charm.on.upgrade_relation_changed, self._on_upgrade_changed)
        self.framework.observe(
            self.charm.on.postgresql_pebble_ready, self._on_postgresql_pebble_ready
        )
        self.framework.observe(self.charm.on.upgrade_charm, self._on_upgrade_charm_check_legacy)

    def _handle_label_change(self) -> None:
        """Handle the label change from `master` to `primary`."""
        unit_number = int(self.charm.unit.name.split("/")[1])
        if unit_number == 1:
            # If the unit is the last to be upgraded before unit zero,
            # trigger a switchover, so one of the upgraded units becomes
            # the primary.
            try:
                self.charm._patroni.switchover()
            except SwitchoverFailedError as e:
                logger.warning(f"Switchover failed: {e}")
        if len(self.charm._peers.units) == 0 or unit_number == 1:
            # If the unit is the last to be upgraded before unit zero
            # or the only unit in the cluster, update the label.
            self.charm._create_services()

    @property
    def is_no_sync_member(self) -> bool:
        """Whether this member shouldn't be a synchronous standby (when it's a replica)."""
        if not self.peer_relation:
            return False

        sync_standbys = self.peer_relation.data[self.charm.app].get("sync-standbys")
        if sync_standbys is None:
            return False
        return self.charm.unit.name not in json.loads(sync_standbys)

    @override
    def log_rollback_instructions(self) -> None:
        """Log rollback instructions."""
        logger.info(
            "Run `juju refresh --revision <previous-revision> postgresql-k8s` to initiate the rollback"
        )
        logger.info(
            "and `juju run-action postgresql-k8s/leader resume-upgrade` to resume the rollback"
        )

    def _on_postgresql_pebble_ready(self, event: WorkloadEvent) -> None:
        """Handle pebble ready event.

        Confirm that unit is healthy and set unit completed.
        """
        if not self.peer_relation:
            logger.debug("Deferring on_pebble_ready: no upgrade peer relation yet")
            event.defer()
            return

        if self.state not in ["upgrading", "recovery"]:
            return

        # Don't mark the upgrade of this unit as completed until Patroni reports the
        # workload is ready.
        if not self.charm._patroni.member_started:
            logger.debug("Deferring on_pebble_ready: Patroni has not started yet")
            event.defer()
            return

        if self.charm.unit.is_leader():
            if not self.charm._patroni.primary_endpoint_ready:
                logger.debug(
                    "Deferring on_pebble_ready: current unit is leader but primary endpoint is not ready yet"
                )
                event.defer()
                return
            self._set_up_new_credentials_for_legacy()
            self._set_up_new_access_roles_for_legacy()

        try:
            for attempt in Retrying(stop=stop_after_attempt(6), wait=wait_fixed(10)):
                with attempt:
                    if (
                        self.charm.unit.name.replace("/", "-")
                        in self.charm._patroni.cluster_members
                        and self.charm._patroni.is_replication_healthy
                    ):
                        self._handle_label_change()
                        logger.debug("Upgraded unit is healthy. Set upgrade state to `completed`")
                        self.set_unit_completed()
                    else:
                        logger.debug(
                            "Instance not yet back in the cluster or not healthy."
                            f" Retry {attempt.retry_state.attempt_number}/6"
                        )
                        raise Exception
        except RetryError:
            logger.error("Upgraded unit is not part of the cluster or not healthy")
            self.set_unit_failed()
            self.charm.unit.status = BlockedStatus(
                "upgrade failed. Check logs for rollback instruction"
            )

    def _on_upgrade_changed(self, event) -> None:
        """Update the Patroni nosync tag in the unit if needed."""
        if not self.peer_relation or not self.charm._patroni.member_started:
            return

        self.charm.update_config()
        self.charm.updated_synchronous_node_count()

    def _on_upgrade_charm_check_legacy(self, event: UpgradeCharmEvent) -> None:
        if not self.peer_relation:
            logger.debug("Wait all units join the upgrade relation")
            return

        if self.state:
            # Do nothing - if state set, upgrade is supported
            return

        logger.warning("Upgrading from unspecified version")

        # All peers should set the state to upgrading.
        self.unit_upgrade_data.update({"state": "upgrading"})

        if self.charm.unit.name != f"{self.charm.app.name}/{self.charm.app.planned_units() - 1}":
            self.charm.unit.status = MaintenanceStatus("upgrading unit")
            self.peer_relation.data[self.charm.unit].update({"state": "upgrading"})
            self._set_rolling_update_partition(self.charm.app.planned_units())

    @override
    def pre_upgrade_check(self) -> None:
        """Runs necessary checks validating the cluster is in a healthy state to upgrade.

        Called by all units during :meth:`_on_pre_upgrade_check_action`.

        Raises:
            :class:`ClusterNotReadyError`: if cluster is not ready to upgrade
        """
        default_message = "Pre-upgrade check failed and cannot safely upgrade"
        if not self.charm._patroni.are_all_members_ready():
            raise ClusterNotReadyError(
                default_message,
                "not all members are ready yet",
                "wait for all units to become active/idle",
            )

        if self.charm._patroni.is_creating_backup:
            raise ClusterNotReadyError(
                default_message,
                "a backup is being created",
                "wait for the backup creation to finish before starting the upgrade",
            )

        # If the first unit is already the primary we don't need to do any
        # switchover.
        primary_unit_name = self.charm._patroni.get_primary(unit_name_pattern=True)
        unit_zero_name = f"{self.charm.app.name}/0"
        if primary_unit_name == unit_zero_name:
            # Should be replaced with refresh v3
            self.peer_relation.data[self.charm.app].update({"sync-standbys": ""})  # type: ignore
            self._set_first_rolling_update_partition()
            return

        sync_standby_names = self.charm._patroni.get_sync_standby_names()
        if len(sync_standby_names) == 0:
            raise ClusterNotReadyError("invalid number of sync nodes", "no action!")

        # If the first unit is a sync-standby we can switchover to it.
        if unit_zero_name in sync_standby_names:
            try:
                # Should be replaced with refresh v3
                self.peer_relation.data[self.charm.app].update({"sync-standbys": ""})  # type: ignore
                self.charm._patroni.switchover(unit_zero_name)
            except SwitchoverFailedError as e:
                raise ClusterNotReadyError(
                    str(e), f"try to switchover manually to {unit_zero_name}"
                ) from e
            self._set_first_rolling_update_partition()
            return

        # If the first unit is not one of the sync-standbys, make it one and request
        # the action to be executed again (because relation data need to be propagated
        # to the other units to make some of them simple replicas and enable the fist
        # unit to become a sync-standby before we can trigger a switchover to it).
        self._set_list_of_sync_standbys()
        cause = f"{unit_zero_name} needs to be a synchronous standby in order to become the primary before the upgrade process can start"
        resolution = f"wait 30 seconds for {unit_zero_name} to become a synchronous standby and run this action again"
        action_message = f"{cause} - {resolution}"
        raise ClusterNotReadyError(action_message, cause, resolution)

    def _set_list_of_sync_standbys(self) -> None:
        """Set the list of desired sync-standbys in the relation data."""
        if self.charm.app.planned_units() > 2:
            sync_standbys = self.charm._patroni.get_sync_standby_names()
            # Include the first unit as one of the sync-standbys.
            unit_to_become_sync_standby = f"{self.charm.app.name}/0"
            if unit_to_become_sync_standby not in set(sync_standbys):
                if len(sync_standbys) > 0:
                    sync_standbys.pop()
                sync_standbys.append(unit_to_become_sync_standby)
            # Should be replaced with refresh v3
            self.peer_relation.data[self.charm.app].update({  # type: ignore
                "sync-standbys": json.dumps(sync_standbys)
            })
            logger.debug(f"sync-standbys changed to: {sync_standbys}")

    @override
    def _set_rolling_update_partition(self, partition: int) -> None:
        """Set the rolling update partition to a specific value."""
        try:
            patch = {"spec": {"updateStrategy": {"rollingUpdate": {"partition": partition}}}}
            Client().patch(
                StatefulSet,
                name=self.charm.model.app.name,
                namespace=self.charm.model.name,
                obj=patch,
            )
            logger.debug(f"Kubernetes StatefulSet partition set to {partition}")
        except ApiError as e:
            cause = "`juju trust` needed" if e.status.code == 403 else str(e)
            raise KubernetesClientError("Kubernetes StatefulSet patch failed", cause) from e

    def _set_first_rolling_update_partition(self) -> None:
        """Set the initial rolling update partition value."""
        try:
            self._set_rolling_update_partition(self.charm.app.planned_units() - 1)
        except KubernetesClientError as e:
            raise ClusterNotReadyError(e.message, e.cause) from e

    def _set_up_new_access_roles_for_legacy(self) -> None:
        """Create missing access groups and their memberships."""
        access_groups = self.charm.postgresql.list_access_groups()
        if access_groups == set(ACCESS_GROUPS) and sorted(
            self.charm.postgresql.list_users_from_relation()
        ) == sorted(self.charm.postgresql.list_users(group="relation_access")):
            return

        self.charm.postgresql.create_access_groups()
        self.charm.postgresql.grant_internal_access_group_memberships()
        self.charm.postgresql.grant_relation_access_group_memberships()

    def _set_up_new_credentials_for_legacy(self) -> None:
        """Create missing password and user."""
        for key in (MONITORING_PASSWORD_KEY, PATRONI_PASSWORD_KEY):
            if self.charm.get_secret(APP_SCOPE, key) is None:
                self.charm.set_secret(APP_SCOPE, key, new_password())
        users = self.charm.postgresql.list_users()
        if MONITORING_USER not in users:
            self.charm.postgresql.create_user(
                MONITORING_USER,
                self.charm.get_secret(APP_SCOPE, MONITORING_PASSWORD_KEY),
                extra_user_roles="pg_monitor",
            )

    @property
    def unit_upgrade_data(self) -> RelationDataContent:
        """Return the application upgrade data."""
        # Should be replaced with refresh v3
        return self.peer_relation.data[self.charm.unit]  # type: ignore
