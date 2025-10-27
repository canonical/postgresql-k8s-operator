# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Refresh logic for postgresql-k8s operator charm."""

import dataclasses
import logging
from typing import TYPE_CHECKING

import charm_refresh
from charm_refresh import CharmSpecificKubernetes, CharmVersion

if TYPE_CHECKING:
    from charm import PostgresqlOperatorCharm

logger = logging.getLogger(__name__)


@dataclasses.dataclass(eq=False)
class PostgreSQLRefresh(CharmSpecificKubernetes):
    """Base class for PostgreSQL refresh operations."""

    _charm: "PostgresqlOperatorCharm"

    @classmethod
    def is_compatible(
        cls,
        *,
        old_charm_version: CharmVersion,
        new_charm_version: CharmVersion,
        old_workload_version: str,
        new_workload_version: str,
    ) -> bool:
        """Checks charm version compatibility."""
        if not super().is_compatible(
            old_charm_version=old_charm_version,
            new_charm_version=new_charm_version,
            old_workload_version=old_workload_version,
            new_workload_version=new_workload_version,
        ):
            logger.error("Charm version is not compatible 1")
            return False

        # Check workload version compatibility
        old_major, old_minor = (int(component) for component in old_workload_version.split("."))
        new_major, new_minor = (int(component) for component in new_workload_version.split("."))
        if old_major != new_major:
            logger.error(f"Charm version is not compatible 2: {old_major} != {new_major}")
            return False
        logger.error(f"old_minor: {old_minor}, new_minor: {new_minor}")
        return new_minor >= old_minor

    def run_pre_refresh_checks_after_1_unit_refreshed(self) -> None:
        """Implement pre-refresh checks after 1 unit refreshed."""
        logger.debug("Running pre-refresh checks")
        if self._charm._patroni.are_all_members_ready():
            raise charm_refresh.PrecheckFailed("Not all members are ready yet.")
        if self._charm._patroni.is_creating_backup:
            raise charm_refresh.PrecheckFailed("A backup is being created.")

    #     # If the first unit is already the primary we don't need to do any
    #     # switchover.
    #     primary_unit_name = self._charm._patroni.get_primary(unit_name_pattern=True)
    #     unit_zero_name = f"{self._charm.app.name}/0"
    #     if primary_unit_name == unit_zero_name:
    #         self.peer_relation.data[self._charm.app].update({"sync-standbys": ""})
    #         self._set_first_rolling_update_partition()
    #         return
    #
    #     sync_standby_names = self._charm._patroni.get_sync_standby_names()
    #     if len(sync_standby_names) == 0:
    #         raise charm_refresh.PrecheckFailed("invalid number of sync nodes", "no action!")
    #
    #     # If the first unit is a sync-standby we can switchover to it.
    #     if unit_zero_name in sync_standby_names:
    #         try:
    #             self.peer_relation.data[self._charm.app].update({"sync-standbys": ""})
    #             self._charm._patroni.switchover(unit_zero_name)
    #         except SwitchoverFailedError as e:
    #             raise charm_refresh.PrecheckFailed(
    #                 str(e), f"try to switchover manually to {unit_zero_name}"
    #             ) from e
    #         self._set_first_rolling_update_partition()
    #         return
    #
    #     # If the first unit is not one of the sync-standbys, make it one and request
    #     # the action to be executed again (because relation data need to be propagated
    #     # to the other units to make some of them simple replicas and enable the fist
    #     # unit to become a sync-standby before we can trigger a switchover to it).
    #     self._set_list_of_sync_standbys()
    #     cause = f"{unit_zero_name} needs to be a synchronous standby in order to become the primary before the upgrade process can start"
    #     resolution = f"wait 30 seconds for {unit_zero_name} to become a synchronous standby and run this action again"
    #     action_message = f"{cause} - {resolution}"
    #     raise charm_refresh.PrecheckFailed(action_message, cause, resolution)
    #
    # def _set_list_of_sync_standbys(self) -> None:
    #     """Set the list of desired sync-standbys in the relation data."""
    #     if self._charm.app.planned_units() > 2:
    #         sync_standbys = self._charm._patroni.get_sync_standby_names()
    #         # Include the first unit as one of the sync-standbys.
    #         unit_to_become_sync_standby = f"{self._charm.app.name}/0"
    #         if unit_to_become_sync_standby not in set(sync_standbys):
    #             if len(sync_standbys) > 0:
    #                 sync_standbys.pop()
    #             sync_standbys.append(unit_to_become_sync_standby)
    #         self.peer_relation.data[self._charm.app].update({
    #             "sync-standbys": json.dumps(sync_standbys)
    #         })
    #         logger.debug(f"sync-standbys changed to: {sync_standbys}")
