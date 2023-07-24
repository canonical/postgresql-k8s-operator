# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Upgrades implementation."""
import json
import logging

from charms.data_platform_libs.v0.upgrade import (
    ClusterNotReadyError,
    DataUpgrade,
    DependencyModel,
    KubernetesClientError,
)
from lightkube.core.client import Client
from lightkube.core.exceptions import ApiError
from lightkube.resources.apps_v1 import StatefulSet
from ops.charm import RelationChangedEvent, WorkloadEvent
from pydantic import BaseModel
from typing_extensions import override

from patroni import SwitchoverFailedError

logger = logging.getLogger(__name__)


class PostgreSQLDependencyModel(BaseModel):
    """PostgreSQL dependencies model."""

    charm: DependencyModel


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
            getattr(self.charm.on, "postgresql_pebble_ready"), self._on_postgresql_pebble_ready
        )

    @property
    def is_no_sync_member(self) -> bool:
        """Whether this member shouldn't be a synchronous standby (when it's a replica)."""
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
            logger.error("Deferring on_pebble_ready: no upgrade peer relation yet")
            event.defer()
            return

        if self.peer_relation.data[self.charm.unit].get("state") != "upgrading":
            return

        if not self.charm._patroni.member_started:
            logger.error("Deferring on_pebble_ready: Patroni has not started yet")
            event.defer()
            return

        logger.error("called set_unit_completed")
        self.set_unit_completed()

    def _on_upgrade_changed(self, event: RelationChangedEvent) -> None:
        if not self.peer_relation:
            event.defer()
            return

        self.charm.update_config()

    @override
    def pre_upgrade_check(self) -> None:
        """Runs necessary checks validating the cluster is in a healthy state to upgrade.

        Called by all units during :meth:`_on_pre_upgrade_check_action`.

        Raises:
            :class:`ClusterNotReadyError`: if cluster is not ready to upgrade
        """
        if not self.charm._patroni.are_all_members_ready():
            raise ClusterNotReadyError(
                "not all members are ready yet", "wait for all units to become active/idle"
            )

        # check for backups running.

        primary_unit_name = self.charm._patroni.get_primary(unit_name_pattern=True)
        unit_zero_name = f"{self.charm.app.name}/0"
        if primary_unit_name == unit_zero_name:
            self.peer_relation.data[self.charm.app].update({"sync-standbys": ""})
            self._set_rolling_update_partition(self.charm.app.planned_units() - 1)
            return

        sync_standby_names = self.charm._patroni.get_sync_standby_names()
        if len(sync_standby_names) == 0:
            raise ClusterNotReadyError("invalid number of sync nodes", "no action!")

        if unit_zero_name in sync_standby_names:
            try:
                self.peer_relation.data[self.charm.app].update({"sync-standbys": ""})
                self.charm._patroni.switchover(unit_zero_name)
            except SwitchoverFailedError as e:
                raise ClusterNotReadyError(
                    str(e), f"try to switchover manually to {unit_zero_name}"
                )
            self._set_rolling_update_partition(self.charm.app.planned_units() - 1)
            return

        self._set_list_of_sync_standbys()
        raise ClusterNotReadyError(
            f"{unit_zero_name} needs to be a synchronous standby in order to become the primary before the upgrade process can start",
            f"wait 30 seconds for {unit_zero_name} and run this action again",
        )

    def _set_list_of_sync_standbys(self) -> None:
        if self.charm.app.planned_units() > 2:
            sync_standbys = self.charm._patroni.get_sync_standby_names()
            unit_to_become_sync_standby = f"{self.charm.app.name}/0"
            if unit_to_become_sync_standby not in set(sync_standbys):
                if len(sync_standbys) > 0:
                    sync_standbys.pop()
                sync_standbys.append(unit_to_become_sync_standby)
            self.peer_relation.data[self.charm.app].update(
                {"sync-standbys": json.dumps(sync_standbys)}
            )
            logger.debug(f"sync-standbys changed to: {sync_standbys}")

    @override
    def _set_rolling_update_partition(self, partition: int) -> None:
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
            if e.status.code == 403:
                cause = "`juju trust` needed"
            else:
                cause = str(e)
            raise KubernetesClientError("Kubernetes StatefulSet patch failed", cause)
