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
        if not self._charm._patroni.are_all_members_ready():
            raise charm_refresh.PrecheckFailed("Not all members are ready yet.")
        if self._charm._patroni.is_creating_backup:
            raise charm_refresh.PrecheckFailed("A backup is being created.")

    def run_pre_refresh_checks_before_any_units_refreshed(self) -> None:
        """Implement pre-refresh checks before any unit refreshed."""
        self.run_pre_refresh_checks_after_1_unit_refreshed()

        # If the first unit is not the primary we ask the user to switchover
        # the primary to it.
        primary_unit_name = self._charm._patroni.get_primary(unit_name_pattern=True)
        unit_zero_name = f"{self._charm.app.name}/0"
        if primary_unit_name != unit_zero_name:
            raise charm_refresh.PrecheckFailed(
                f"Switch primary to {unit_zero_name} to avoid multiple switchovers during refresh."
            )
