# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Refresh logic for postgresql-k8s operator charm."""

import dataclasses
from typing import TYPE_CHECKING

from charm_refresh import CharmSpecificKubernetes, CharmVersion

if TYPE_CHECKING:
    from charm import PostgresqlOperatorCharm


@dataclasses.dataclass(eq=False)
class _PostgreSQLRefresh(CharmSpecificKubernetes):
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
        # Check charm version compatibility
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
        # if self._charm.backup_in_progress:
        pass
