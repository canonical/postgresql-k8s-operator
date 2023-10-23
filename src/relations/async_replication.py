# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Implements the state-machine.

1) First async replication relation is made: both units get blocked waiting for a leader
2) User runs the promote action against one of the clusters
3) The cluster moves leader and sets the async-replication data, marking itself as leader
4) The other units receive that new information and update themselves to become standby-leaders.
"""

import json
import logging
from typing import Dict, Set

from lightkube import Client
from lightkube.resources.core_v1 import Endpoints
from ops.charm import (
    ActionEvent,
    CharmBase,
)
from ops.framework import Object
from ops.model import (
    Unit,
)

logger = logging.getLogger(__name__)


ASYNC_PRIMARY_RELATION = "async-primary"
ASYNC_REPLICA_RELATION = "async-replica"


class MoreThanOnePrimarySelectedError(Exception):
    """Represents more than one primary has been selected."""


class PostgreSQLAsyncReplication(Object):
    """Defines the async-replication management logic."""

    def __init__(self, charm: CharmBase, relation_name: str = ASYNC_PRIMARY_RELATION) -> None:
        super().__init__(charm, relation_name)
        self.relation_name = relation_name
        self.charm = charm
        self.framework.observe(
            self.charm.on[ASYNC_PRIMARY_RELATION].relation_changed, self._on_primary_changed
        )
        self.framework.observe(
            self.charm.on[ASYNC_REPLICA_RELATION].relation_changed, self._on_primary_changed
        )
        self.framework.observe(
            self.charm.on.promote_standby_cluster_action, self._on_promote_standby_cluster
        )
        self.framework.observe(
            self.charm.on.demote_primary_cluster_action, self._on_demote_primary_cluster
        )

        # We treat both relations above as actually the same.
        # The big difference appears only at promote/demote actions
        self.relation_set = {
            *set(self.charm.model.relations[ASYNC_PRIMARY_RELATION]),
            *set(self.charm.model.relations[ASYNC_REPLICA_RELATION]),
        }
        self.container = self.charm.unit.get_container("postgresql")

    @property
    def endpoint(self) -> str:
        """Assumes the endpoint is the same, disregard if we are a primary or standby cluster."""
        for rel in self.relation_set:
            return str(self.charm.model.get_binding(rel).network.ingress_address)
        return None

    def standby_endpoints(self) -> Set[str]:
        """Returns the set of IPs used by each standby unit with a /32 mask."""
        standby_endpoints = set()
        for rel in self.relation_set:
            for unit in rel.units:
                if not rel.data[unit].get("elected", None):
                    standby_endpoints.add("{}/32".format(str(rel.data[unit]["ingress-address"])))
        return standby_endpoints

    def get_primary_data(self) -> Dict[str, str]:
        """Returns the primary info, if available."""
        for rel in self.relation_set:
            for unit in rel.units:
                if unit.name == self.charm.unit.name:
                    # If this unit is the leader, then return None
                    return None
                if rel.data[unit].get("elected", None):
                    elected_data = json.loads(rel.data[unit]["elected"])
                    return {
                        "endpoint": str(elected_data["endpoint"]),
                        "replication-password": elected_data["replication-password"],
                        "superuser-password": elected_data["superuser-password"],
                    }
        return None

    def _on_primary_changed(self, _):
        """Triggers a configuration change."""
        primary = self._check_if_primary_already_selected()
        if not primary:
            return

        if primary.name == self.charm.unit.name:
            # This unit is the leader, generate  a new configuration and leave.
            # There is nothing to do for the leader.
            self.charm.update_config()
            self.container.start(self.charm._postgresql_service)
            return

        self.container.stop(self.charm._postgresql_service)

        # Standby units must delete their data folder
        # Delete the K8S endpoints that tracks the cluster information, including its id.
        # This is the same as "patronictl remove patroni-postgresql-k8s", but the latter doesn't
        # work after the database service is stopped on Pebble.
        try:
            client = Client()
            client.delete(
                Endpoints,
                name=f"patroni-{self.charm._name}",
                namespace=self.charm._namespace,
            )
            client.delete(
                Endpoints,
                name=f"patroni-{self.charm._name}-config",
                namespace=self.charm._namespace,
            )

            self.container.exec("rm -r /var/lib/postgresql/data/pgdata".split()).wait_output()
            self.charm._create_pgdata(self.container)

            self.charm.update_config()
        except Exception:
            pass
        self.container.start(self.charm._postgresql_service)

    def _get_primary_candidates(self):
        rel = self.model.get_relation(ASYNC_PRIMARY_RELATION)
        return rel.units if rel else []

    def _check_if_primary_already_selected(self) -> Unit:
        """Returns the unit if a primary is present."""
        result = None
        if not self.relation_set:
            return None
        for rel in self.relation_set:
            for unit in rel.units:
                if "elected" in rel.data[unit] and not result:
                    result = unit
                elif result:
                    raise MoreThanOnePrimarySelectedError
        return result

    def _on_promote_standby_cluster(self, event: ActionEvent) -> None:
        """Moves a standby cluster to a primary, if none is present."""
        if (
            "cluster_initialised" not in self.charm._peers.data[self.charm.app]
            or not self.charm._patroni.member_started
        ):
            event.fail("Cluster not initialized yet.")
            return

        if not self.charm.unit.is_leader():
            event.fail("Not the charm leader unit.")
            return

        # Let the exception error the unit
        unit = self._check_if_primary_already_selected()
        if unit:
            event.fail(f"Cannot promote - {unit.name} is already primary: demote it first")
            return

        # If this is a standby-leader, then execute switchover logic
        # TODO

        # Now, publish that this unit is the leader
        if not self.endpoint:
            event.fail("No relation found.")
            return
        primary_relation = self.model.get_relation(ASYNC_PRIMARY_RELATION)
        if not primary_relation:
            event.fail("No primary relation")
            return

        primary_relation.data[self.charm.unit]["elected"] = json.dumps(
            {
                "endpoint": self.endpoint,
                "replication-password": self.charm._patroni._replication_password,
                "superuser-password": self.charm._patroni._superuser_password,
            }
        )
        # event.set_result()

    def _on_demote_primary_cluster(self, event: ActionEvent) -> None:
        """Moves a primary cluster to standby."""
        if (
            "cluster_initialised" not in self.charm._peers.data[self.charm.app]
            or not self.charm._patroni.member_started
        ):
            event.fail("Cluster not initialized yet.")
            return

        if not self.charm.unit.is_leader():
            event.fail("Not the charm leader unit.")
            return

        # Let the exception error the unit
        unit = self._check_if_primary_already_selected()
        if not unit or unit.name != self.charm.unit.name:
            event.fail(f"Cannot promote - {unit.name} is primary")
            return

        # If this is a standby-leader, then execute switchover logic
        # TODO

        # Now, publish that this unit is the leader
        del self._get_primary_candidates()[self.charm.unit].data["elected"]
        # event.set_result()
