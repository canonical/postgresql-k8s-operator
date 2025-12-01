# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Refresh logic for postgresql-k8s operator charm."""

import dataclasses
import logging
from typing import TYPE_CHECKING

import charm_refresh
from charm_refresh import CharmSpecificKubernetes, CharmVersion

from patroni import SwitchoverFailedError

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
        """Checks charm and workload version compatibility."""
        if not super().is_compatible(
            old_charm_version=old_charm_version,
            new_charm_version=new_charm_version,
            old_workload_version=old_workload_version,
            new_workload_version=new_workload_version,
        ):
            return False

        # Check workload version compatibility
        old_major, old_minor = (int(component) for component in old_workload_version.split("."))
        new_major, new_minor = (int(component) for component in new_workload_version.split("."))
        if old_major != new_major:
            return False
        return new_minor >= old_minor

    def run_pre_refresh_checks_after_1_unit_refreshed(self) -> None:
        """Implement pre-refresh checks after 1 unit refreshed."""
        logger.debug("Running pre-refresh checks")
        if self._charm._patroni.is_creating_backup:
            raise charm_refresh.PrecheckFailed("Backup in progress")

        # Check if all units except the highest unit (first to be refreshed) are online.
        running_members = self._charm._patroni.get_running_cluster_members()

        # The highest unit number is planned_units - 1 (e.g., if 3 units, highest is unit 2).
        # Members are named like "postgresql-k8s-0", "postgresql-k8s-1", etc.
        highest_unit_number = self._charm.app.planned_units() - 1

        # Check if all units except the highest unit are online.
        for unit_number in range(self._charm.app.planned_units()):
            member_name = f"{self._charm.app.name}-{unit_number}"
            if unit_number != highest_unit_number and member_name not in running_members:
                raise charm_refresh.PrecheckFailed(
                    f"PostgreSQL is not running on unit {unit_number}"
                )

        # Switch primary to last unit to refresh (lowest unit number).
        last_unit_to_refresh = f"{self._charm.app.name}/0"
        if self._charm._patroni.get_primary(unit_name_pattern=True) == last_unit_to_refresh:
            logger.info(
                f"Unit {last_unit_to_refresh} was already primary during pre-refresh check"
            )
        else:
            try:
                self._charm._patroni.switchover(
                    candidate=last_unit_to_refresh,
                    async_cluster=bool(
                        self._charm.async_replication.get_primary_cluster_endpoint()
                    ),
                )
            except SwitchoverFailedError as e:
                logger.warning(f"switchover failed with reason: {e}")
                raise charm_refresh.PrecheckFailed("Unable to switch primary") from None
            else:
                logger.info(
                    f"Switched primary to unit {last_unit_to_refresh} during pre-refresh check"
                )

    def run_pre_refresh_checks_before_any_units_refreshed(self) -> None:
        """Implement pre-refresh checks before any unit refreshed."""
        if not self._charm._patroni.are_all_members_ready():
            raise charm_refresh.PrecheckFailed("PostgreSQL is not running on 1+ units")

        self.run_pre_refresh_checks_after_1_unit_refreshed()
