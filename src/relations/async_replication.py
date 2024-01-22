# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Implements the state-machine.

1) First async replication relation is made: both units get blocked waiting for a leader
2) User runs the promote action against one of the clusters
3) The cluster moves leader and sets the async-replication data, marking itself as leader
4) The other units receive that new information and update themselves to become standby-leaders.
"""

import json
import logging
from datetime import datetime
from typing import Dict, Optional, Set, Tuple

from lightkube import Client
from lightkube.resources.core_v1 import Service
from ops.charm import (
    ActionEvent,
    CharmBase,
)
from ops.framework import Object
from ops.model import (
    Unit,
    WaitingStatus,
)
from ops.pebble import ChangeError
from tenacity import Retrying, stop_after_attempt, wait_fixed

from constants import (
    APP_SCOPE,
    REPLICATION_PASSWORD_KEY,
    USER_PASSWORD_KEY,
    WORKLOAD_OS_GROUP,
    WORKLOAD_OS_USER,
)
from coordinator_ops import CoordinatedOpsManager

logger = logging.getLogger(__name__)


ASYNC_PRIMARY_RELATION = "async-primary"
ASYNC_REPLICA_RELATION = "async-replica"


class MoreThanOnePrimarySelectedError(Exception):
    """Represents more than one primary has been selected."""


def _get_pod_ip():
    """Reads some files to quickly figure out its own pod IP.

    It should work for any Ubuntu-based image
    """
    with open("/etc/hosts") as f:
        hosts = f.read()
    with open("/etc/hostname") as f:
        hostname = f.read().replace("\n", "")
    line = [ln for ln in hosts.split("\n") if ln.find(hostname) >= 0][0]
    return line.split("\t")[0]


class PostgreSQLAsyncReplication(Object):
    """Defines the async-replication management logic."""

    def __init__(self, charm: CharmBase, relation_name: str = ASYNC_PRIMARY_RELATION) -> None:
        super().__init__(charm, relation_name)
        self.relation_name = relation_name
        self.charm = charm
        self.restart_coordinator = CoordinatedOpsManager(charm, "restart", tag="_asyncreplica")
        self.framework.observe(
            self.charm.on[ASYNC_PRIMARY_RELATION].relation_changed, self._on_primary_changed
        )
        self.framework.observe(
            self.charm.on[ASYNC_REPLICA_RELATION].relation_changed, self._on_standby_changed
        )
        self.framework.observe(
            self.restart_coordinator.on.coordinator_requested, self._on_coordination_request
        )
        self.framework.observe(
            self.restart_coordinator.on.coordinator_approved, self._on_coordination_approval
        )

        # Departure events
        self.framework.observe(
            self.charm.on[ASYNC_PRIMARY_RELATION].relation_departed, self._on_departure
        )
        self.framework.observe(
            self.charm.on[ASYNC_REPLICA_RELATION].relation_departed, self._on_departure
        )
        self.framework.observe(
            self.charm.on[ASYNC_PRIMARY_RELATION].relation_broken, self._on_departure
        )
        self.framework.observe(
            self.charm.on[ASYNC_REPLICA_RELATION].relation_broken, self._on_departure
        )

        # Actions
        self.framework.observe(
            self.charm.on.promote_standby_cluster_action, self._on_promote_standby_cluster
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
        sync_standby_names = self.charm._patroni.get_sync_standby_names()
        if len(sync_standby_names) > 0:
            unit = self.model.get_unit(sync_standby_names[0])
            return self.charm.get_unit_ip(unit)
        else:
            return self.charm.get_unit_ip(self.charm.unit)

    def standby_endpoints(self) -> Set[str]:
        """Returns the set of IPs used by each standby unit with a /32 mask."""
        standby_endpoints = set()
        for rel in self.relation_set:
            for unit in self._all_units(rel):
                if not rel.data[unit].get("elected", None):
                    standby_endpoints.add("{}/32".format(str(rel.data[unit]["ingress-address"])))
                    if "pod-address" in rel.data[unit]:
                        standby_endpoints.add("{}/32".format(str(rel.data[unit]["pod-address"])))
        return standby_endpoints

    def get_primary_data(self) -> Optional[Dict[str, str]]:
        """Returns the primary info, if available and if the primary cluster is ready."""
        for rel in self.relation_set:
            for unit in self._all_units(rel):
                if "elected" in rel.data[unit] and unit.name == self.charm.unit.name:
                    # If this unit is the leader, then return None
                    return None

                if rel.data[unit].get("elected", None) and rel.data[unit].get(
                    "primary-cluster-ready", None
                ):
                    elected_data = json.loads(rel.data[unit]["elected"])
                    return {
                        "endpoint": str(elected_data["endpoint"]),
                        "replication-password": elected_data["replication-password"],
                        "superuser-password": elected_data["superuser-password"],
                    }
        return None

    def _all_units(self, relation):
        found_units = {*relation.units, self.charm.unit}
        logger.debug(f"Units found: {found_units}")
        return found_units

    def _all_replica_published_pod_ips(self) -> bool:
        for rel in self.relation_set:
            for unit in self._all_units(rel):
                if "elected" in rel.data[unit]:
                    # This is the leader unit, it will not publish its own pod address
                    continue
                if "pod-address" not in rel.data[unit]:
                    return False
        return True

    def _on_departure(self, _):
        for rel in [
            self.model.get_relation(ASYNC_REPLICA_RELATION),
            self.model.get_relation(ASYNC_PRIMARY_RELATION),
        ]:
            if not rel:  # if no relation exits, then it rel == None
                continue
            if "pod-address" in rel.data[self.charm.unit]:
                del rel.data[self.charm.unit]["pod-address"]
            if "elected" in rel.data[self.charm.unit]:
                del rel.data[self.charm.unit]["elected"]
            if "primary-cluster-ready" in rel.data[self.charm.unit]:
                del rel.data[self.charm.unit]["primary-cluster-ready"]
        if self.charm.unit.is_leader() and "promoted" in self.charm.app_peer_data:
            del self.charm.app_peer_data["promoted"]

        for attempt in Retrying(stop=stop_after_attempt(5), wait=wait_fixed(3)):
            with attempt:
                self.container.stop(self.charm._postgresql_service)
        self.charm.update_config()
        self.container.start(self.charm._postgresql_service)

    def _on_primary_changed(self, event):
        """Triggers a configuration change in the primary units."""
        primary_relation = self.model.get_relation(ASYNC_PRIMARY_RELATION)
        if not primary_relation:
            return
        logger.info("_on_primary_changed: primary_relation exists")

        primary = self._check_if_primary_already_selected()
        if not primary:
            # primary may not be available because the action of promoting a cluster was
            # executed way after the relation changes.
            # Defer it until
            event.defer()
            return
        logger.info("_on_primary_changed: primary cluster exists")

        if primary.name != self.charm.unit.name:
            # this unit is not the system leader
            return
        logger.info("_on_primary_changed: unit is the primary's leader")

        if not self._all_replica_published_pod_ips():
            # We will have more events happening, no need for retrigger
            event.defer()
            return
        logger.info("_on_primary_changed: all replicas published pod details")

        # This unit is the leader, generate  a new configuration and leave.
        # There is nothing to do for the leader.
        for attempt in Retrying(stop=stop_after_attempt(5), wait=wait_fixed(3)):
            with attempt:
                self.container.stop(self.charm._postgresql_service)
        self.charm.update_config()
        self.container.start(self.charm._postgresql_service)

        # Retrigger the other units' async-replica-changed
        primary_relation.data[self.charm.unit]["primary-cluster-ready"] = "True"

    def _on_standby_changed(self, event):  # noqa C901
        """Triggers a configuration change."""
        replica_relation = self.model.get_relation(ASYNC_REPLICA_RELATION)
        if not replica_relation:
            return
        logger.info("_on_standby_changed: replica relation available")

        primary = self._check_if_primary_already_selected()
        if not primary:
            return
        logger.info("_on_standby_changed: primary is present")

        # Check if we have already published pod-address. If not, then we are waiting
        # for the leader to catch all the pod ips and restart itself
        if "pod-address" not in replica_relation.data[self.charm.unit]:
            replica_relation.data[self.charm.unit]["pod-address"] = _get_pod_ip()
            # Finish here and wait for the retrigger from the primary cluster
            event.defer()
            return
        logger.info("_on_standby_changed: pod-address published in own replica databag")

        primary_data = self.get_primary_data()
        if not primary_data:
            # We've made thus far.
            # However, the get_primary_data will return != None ONLY if the primary cluster
            # is ready and configured. Until then, we wait.
            event.defer()
            return
        logger.info("_on_standby_changed: primary cluster is ready")

        if "system-id" not in replica_relation.data[self.charm.unit]:
            system_identifier, error = self.get_system_identifier()
            if error is not None:
                raise Exception(f"Failed to get system identifier: {error}")
            replica_relation.data[self.charm.unit]["system-id"] = system_identifier

            if self.charm.unit.is_leader():
                self.charm.set_secret(
                    APP_SCOPE, USER_PASSWORD_KEY, primary_data["superuser-password"]
                )
                self.charm.set_secret(
                    APP_SCOPE, REPLICATION_PASSWORD_KEY, primary_data["replication-password"]
                )
                del self.charm._peers.data[self.charm.app]["cluster_initialised"]

        ################
        # Initiate restart logic
        ################

        # We need to:
        # 1) Stop all standby units
        # 2) Delete the k8s service
        # 3) Remove the pgdata folder
        # 4) Start all standby units
        # For that, the peer leader must first stop its own service and then, issue a
        # coordination request to all units. All units ack that request once they all have
        # their service stopped.
        # Then, we get an approved coordination from the leader, which triggers the
        # steps 2-4.
        if self.charm.unit.is_leader() and not self.restart_coordinator.under_coordination:
            # The leader now requests a ack from each unit that they have stopped.
            self.restart_coordinator.coordinate()

    def _on_coordination_request(self, event):
        # Stop the container.
        # We need all replicas to be stopped, so we can remove the patroni-postgresql-k8s
        # service from Kubernetes and not getting it recreated!
        # We will restart the it once the cluster is ready.
        for attempt in Retrying(stop=stop_after_attempt(5), wait=wait_fixed(3)):
            with attempt:
                self.container.stop(self.charm._postgresql_service)

        replica_relation = self.model.get_relation(ASYNC_REPLICA_RELATION)
        for unit in replica_relation.units:
            if "elected" not in replica_relation.data[unit]:
                continue
            elected_data = json.loads(replica_relation.data[unit]["elected"])
            if "system-id" not in elected_data:
                continue
            if replica_relation.data[self.charm.unit]["system-id"] != elected_data["system-id"]:
                if self.charm.unit.is_leader():
                    # Store current data in a ZIP file, clean folder and generate configuration.
                    logger.info("Creating backup of pgdata folder")
                    self.container.exec(
                        f"tar -zcf /var/lib/postgresql/data/pgdata-{str(datetime.now()).replace(' ', '-').replace(':', '-')}.zip /var/lib/postgresql/data/pgdata".split()
                    ).wait_output()
                logger.info("Removing and recreating pgdata folder")
                self.container.exec("rm -r /var/lib/postgresql/data/pgdata".split()).wait_output()
                self.charm._create_pgdata(self.container)
                break
        self.restart_coordinator.acknowledge(event)

    def _on_coordination_approval(self, event):
        """Runs when the coordinator guaranteed all units have stopped."""
        if self.charm.unit.is_leader():
            # Delete the K8S endpoints that tracks the cluster information, including its id.
            # This is the same as "patronictl remove patroni-postgresql-k8s", but the latter doesn't
            # work after the database service is stopped on Pebble.
            client = Client()
            client.delete(
                Service,
                name=f"patroni-{self.charm._name}-config",
                namespace=self.charm._namespace,
            )
        elif not self.charm._patroni.primary_endpoint_ready:
            self.charm.unit.status = WaitingStatus("waiting for primary to be ready")
            event.defer()
            return

        self.charm.update_config()
        logger.info("_on_standby_changed: configuration done, waiting for restart of the service")

        # We are ready to restart the service now: all peers have configured themselves.
        self.container.start(self.charm._postgresql_service)
        if self.charm.unit.is_leader():
            self.charm._peers.data[self.charm.app]["cluster_initialised"] = "True"

    def _check_if_primary_already_selected(self) -> Optional[Unit]:
        """Returns the unit if a primary is present."""
        result = None
        if not self.relation_set:
            return None
        for rel in self.relation_set:
            for unit in self._all_units(rel):
                if "elected" in rel.data[unit] and not result:
                    result = unit
                elif "elected" in rel.data[unit] and result:
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

        # Now, publish that this unit is the leader
        if not self.endpoint:
            event.fail("No relation found.")
            return
        primary_relation = self.model.get_relation(ASYNC_PRIMARY_RELATION)
        if not primary_relation:
            event.fail("No primary relation")
            return

        # Check if this is a take over from a standby cluster
        if event.params.get("force", False) and event.params.get(
            "force-really-really-mean-it", False
        ):
            pass

        # Let the exception error the unit
        unit = self._check_if_primary_already_selected()
        if unit:
            event.fail(f"Cannot promote - {unit.name} is already primary: demote it first")
            return

        system_identifier, error = self.get_system_identifier()
        if error is not None:
            event.fail(f"Failed to get system identifier: {error}")
            return

        # If this is a standby-leader, then execute switchover logic
        # TODO
        primary_relation.data[self.charm.unit]["elected"] = json.dumps(
            {
                # "endpoint": self.charm.async_replication_endpoint,
                "endpoint": self.endpoint,
                "replication-password": self.charm._patroni._replication_password,
                "superuser-password": self.charm._patroni._superuser_password,
                "system-id": system_identifier,
            }
        )
        self.charm.app_peer_data["promoted"] = "True"

        # Now, check if postgresql it had originally published its pod IP in the
        # replica relation databag. Delete it, if yes.
        replica_relation = self.model.get_relation(ASYNC_PRIMARY_RELATION)
        if not replica_relation or "pod-address" not in replica_relation.data[self.charm.unit]:
            return
        del replica_relation.data[self.charm.unit]["pod-address"]
        # event.set_result()

    def get_system_identifier(self) -> Tuple[Optional[str], Optional[str]]:
        """Returns the PostgreSQL system identifier from this instance."""
        try:
            system_identifier, error = self.container.exec(
                [
                    f'/usr/lib/postgresql/{self.charm._patroni.rock_postgresql_version.split(".")[0]}/bin/pg_controldata',
                    "/var/lib/postgresql/data/pgdata",
                ],
                user=WORKLOAD_OS_USER,
                group=WORKLOAD_OS_GROUP,
            ).wait_output()
        except ChangeError as e:
            return None, str(e)
        if error != "":
            return None, error
        system_identifier = [
            line for line in system_identifier.splitlines() if "Database system identifier" in line
        ][0].split(" ")[-1]
        return system_identifier, None
