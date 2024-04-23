# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Async Replication implementation.

The highest "promoted-cluster-counter" value is used to determine the primary cluster.
The application in any side of the relation which has the highest value in its application
relation databag is considered the primary cluster.

The "unit-promoted-cluster-counter" field in the unit relation databag is used to determine
if the unit is following the promoted cluster. If the value is the same as the highest value
in the application relation databag, then the unit is following the promoted cluster.
Otherwise, it's needed to restart the database in the unit to follow the promoted cluster
if the unit is from the standby cluster (the one that was not promoted).
"""

import itertools
import json
import logging
from datetime import datetime
from typing import List, Optional, Tuple

from lightkube import ApiError, Client
from lightkube.resources.core_v1 import Endpoints, Service
from ops import (
    ActionEvent,
    Application,
    BlockedStatus,
    MaintenanceStatus,
    Object,
    Relation,
    RelationChangedEvent,
    RelationDepartedEvent,
    Secret,
    SecretNotFoundError,
    WaitingStatus,
)
from ops.pebble import ChangeError
from tenacity import RetryError, Retrying, stop_after_delay, wait_fixed

from constants import (
    APP_SCOPE,
    POSTGRESQL_DATA_PATH,
    WORKLOAD_OS_GROUP,
    WORKLOAD_OS_USER,
)
from patroni import ClusterNotPromotedError, NotReadyError, StandbyClusterAlreadyPromotedError

logger = logging.getLogger(__name__)


ASYNC_PRIMARY_RELATION = "async-primary"
ASYNC_REPLICA_RELATION = "async-replica"
READ_ONLY_MODE_BLOCKING_MESSAGE = "Cluster in read-only mode"


class PostgreSQLAsyncReplication(Object):
    """Defines the async-replication management logic."""

    def __init__(self, charm):
        """Constructor."""
        super().__init__(charm, "postgresql")
        self.charm = charm
        self.framework.observe(
            self.charm.on[ASYNC_PRIMARY_RELATION].relation_joined, self._on_async_relation_joined
        )
        self.framework.observe(
            self.charm.on[ASYNC_REPLICA_RELATION].relation_joined, self._on_async_relation_joined
        )
        self.framework.observe(
            self.charm.on[ASYNC_PRIMARY_RELATION].relation_changed, self._on_async_relation_changed
        )
        self.framework.observe(
            self.charm.on[ASYNC_REPLICA_RELATION].relation_changed, self._on_async_relation_changed
        )

        # Departure events
        self.framework.observe(
            self.charm.on[ASYNC_PRIMARY_RELATION].relation_departed,
            self._on_async_relation_departed,
        )
        self.framework.observe(
            self.charm.on[ASYNC_REPLICA_RELATION].relation_departed,
            self._on_async_relation_departed,
        )
        self.framework.observe(
            self.charm.on[ASYNC_PRIMARY_RELATION].relation_broken, self._on_async_relation_broken
        )
        self.framework.observe(
            self.charm.on[ASYNC_REPLICA_RELATION].relation_broken, self._on_async_relation_broken
        )

        # Actions
        self.framework.observe(self.charm.on.promote_cluster_action, self._on_promote_cluster)

        self.container = self.charm.unit.get_container("postgresql")

    def _can_promote_cluster(self, event: ActionEvent) -> bool:
        """Check if the cluster can be promoted."""
        if not self.charm.is_cluster_initialised:
            event.fail("Cluster not initialised yet.")
            return False

        # Check if there is a relation. If not, see if there is a standby leader. If so promote it to leader. If not,
        # fail the action telling that there is no relation and no standby leader.
        relation = self._relation
        if relation is None:
            standby_leader = self.charm._patroni.get_standby_leader()
            if standby_leader is not None:
                try:
                    self.charm._patroni.promote_standby_cluster()
                    if (
                        self.charm.is_blocked
                        and self.charm.unit.status.message == READ_ONLY_MODE_BLOCKING_MESSAGE
                    ):
                        self.charm._peers.data[self.charm.app].update({
                            "promoted-cluster-counter": ""
                        })
                        self.charm._set_active_status()
                except (StandbyClusterAlreadyPromotedError, ClusterNotPromotedError) as e:
                    event.fail(str(e))
                return False
            event.fail("No relation and no standby leader found.")
            return False

        # Check if this cluster is already the primary cluster. If so, fail the action telling that it's already
        # the primary cluster.
        primary_cluster = self._get_primary_cluster()
        if self.charm.app == primary_cluster:
            event.fail("This cluster is already the primary cluster.")
            return False

        # To promote the other cluster if there is already a primary cluster, the action must be called with
        # `force-promotion=true`. If not, fail the action telling that the other cluster is already the primary.
        if relation.app == primary_cluster:
            if not event.params.get("force-promotion"):
                event.fail(
                    f"{relation.app.name} is already the primary cluster. Pass `force-promotion=true` to promote anyway."
                )
                return False
            else:
                logger.warning(
                    "%s is already the primary cluster. Forcing promotion of %s to primary cluster due to `force-promotion=true`.",
                    relation.app.name,
                    self.charm.app.name,
                )

        return True

    def _configure_primary_cluster(
        self, primary_cluster: Application, event: RelationChangedEvent
    ) -> bool:
        """Configure the primary cluster."""
        if self.charm.app == primary_cluster:
            self.charm.update_config()
            if self._is_primary_cluster() and self.charm.unit.is_leader():
                self._update_primary_cluster_data()
                # If this is a standby cluster, remove the information from DCS to make it
                # a normal cluster.
                if self.charm._patroni.get_standby_leader() is not None:
                    self.charm._patroni.promote_standby_cluster()
                    try:
                        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
                            with attempt:
                                if not self.charm.is_primary:
                                    raise ClusterNotPromotedError()
                    except RetryError:
                        logger.debug(
                            "Deferring on_async_relation_changed: standby cluster not promoted yet."
                        )
                        event.defer()
                        return True
            self.charm._peers.data[self.charm.unit].update({
                "unit-promoted-cluster-counter": self._get_highest_promoted_cluster_counter_value()
            })
            self.charm._set_active_status()
            return True
        return False

    def _configure_standby_cluster(self, event: RelationChangedEvent) -> bool:
        """Configure the standby cluster."""
        relation = self._relation
        if relation.name == ASYNC_REPLICA_RELATION:
            # Update the secrets between the clusters.
            primary_cluster_info = relation.data[relation.app].get("primary-cluster-data")
            secret_id = (
                None
                if primary_cluster_info is None
                else json.loads(primary_cluster_info).get("secret-id")
            )
            try:
                secret = self.charm.model.get_secret(id=secret_id, label=self._secret_label)
            except SecretNotFoundError:
                logger.debug("Secret not found, deferring event")
                event.defer()
                return False
            credentials = secret.peek_content()
            for key, password in credentials.items():
                user = key.split("-password")[0]
                self.charm.set_secret(APP_SCOPE, key, password)
                logger.debug("Synced %s password", user)
        system_identifier, error = self.get_system_identifier()
        if error is not None:
            raise Exception(error)
        if system_identifier != relation.data[relation.app].get("system-id"):
            # Store current data in a tar.gz file.
            logger.info("Creating backup of pgdata folder")
            filename = f"{POSTGRESQL_DATA_PATH}-{str(datetime.now()).replace(' ', '-').replace(':', '-')}.tar.gz"
            self.container.exec(
                f"tar -zcf {filename} {POSTGRESQL_DATA_PATH}".split()
            ).wait_output()
            logger.warning("Please review the backup file %s and handle its removal", filename)
        self._remove_previous_cluster_information()
        return True

    def _get_highest_promoted_cluster_counter_value(self) -> str:
        """Return the highest promoted cluster counter."""
        promoted_cluster_counter = "0"
        for async_relation in [
            self.model.get_relation(ASYNC_PRIMARY_RELATION),
            self.model.get_relation(ASYNC_REPLICA_RELATION),
        ]:
            if async_relation is None:
                continue
            for databag in [
                async_relation.data[async_relation.app],
                self.charm._peers.data[self.charm.app],
            ]:
                relation_promoted_cluster_counter = databag.get("promoted-cluster-counter", "0")
                if int(relation_promoted_cluster_counter) > int(promoted_cluster_counter):
                    promoted_cluster_counter = relation_promoted_cluster_counter
        return promoted_cluster_counter

    def _get_primary_cluster(self) -> Optional[Application]:
        """Return the primary cluster."""
        primary_cluster = None
        promoted_cluster_counter = "0"
        for async_relation in [
            self.model.get_relation(ASYNC_PRIMARY_RELATION),
            self.model.get_relation(ASYNC_REPLICA_RELATION),
        ]:
            if async_relation is None:
                continue
            for app, relation_data in {
                async_relation.app: async_relation.data,
                self.charm.app: self.charm._peers.data,
            }.items():
                databag = relation_data[app]
                relation_promoted_cluster_counter = databag.get("promoted-cluster-counter", "0")
                if relation_promoted_cluster_counter > promoted_cluster_counter:
                    promoted_cluster_counter = relation_promoted_cluster_counter
                    primary_cluster = app
        return primary_cluster

    def get_primary_cluster_endpoint(self) -> Optional[str]:
        """Return the primary cluster endpoint."""
        primary_cluster = self._get_primary_cluster()
        if primary_cluster is None or self.charm.app == primary_cluster:
            return None
        relation = self._relation
        primary_cluster_data = relation.data[relation.app].get("primary-cluster-data")
        if primary_cluster_data is None:
            return None
        return json.loads(primary_cluster_data).get("endpoint")

    def _get_secret(self) -> Secret:
        """Return async replication necessary secrets."""
        try:
            # Avoid recreating the secret.
            secret = self.charm.model.get_secret(label=self._secret_label)
            if not secret.id:
                # Workaround for the secret id not being set with model uuid.
                secret._id = f"secret://{self.model.uuid}/{secret.get_info().id.split(':')[1]}"
            return secret
        except SecretNotFoundError:
            logger.debug("Secret not found, creating a new one")
            pass

        app_secret = self.charm.model.get_secret(label=f"{self.model.app.name}.app")
        content = app_secret.peek_content()

        # Filter out unnecessary secrets.
        shared_content = dict(filter(lambda x: "password" in x[0], content.items()))

        return self.charm.model.app.add_secret(content=shared_content, label=self._secret_label)

    def get_standby_endpoints(self) -> List[str]:
        """Return the standby endpoints."""
        relation = self._relation
        primary_cluster = self._get_primary_cluster()
        # List the standby endpoints only for the primary cluster.
        if relation is None or primary_cluster is None or self.charm.app != primary_cluster:
            return []
        return [
            relation.data[unit].get("unit-address")
            for relation in [
                self.model.get_relation(ASYNC_PRIMARY_RELATION),
                self.model.get_relation(ASYNC_REPLICA_RELATION),
            ]
            if relation is not None
            for unit in relation.units
            if relation.data[unit].get("unit-address") is not None
        ]

    def get_system_identifier(self) -> Tuple[Optional[str], Optional[str]]:
        """Returns the PostgreSQL system identifier from this instance."""
        try:
            system_identifier, error = self.container.exec(
                [
                    f'/usr/lib/postgresql/{self.charm._patroni.rock_postgresql_version.split(".")[0]}/bin/pg_controldata',
                    POSTGRESQL_DATA_PATH,
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

    def _get_unit_ip(self) -> str:
        """Reads some files to quickly figure out its own pod IP.

        It should work for any Ubuntu-based image
        """
        with open("/etc/hosts") as f:
            hosts = f.read()
        with open("/etc/hostname") as f:
            hostname = f.read().replace("\n", "")
        line = [ln for ln in hosts.split("\n") if ln.find(hostname) >= 0][0]
        return line.split("\t")[0]

    def _handle_database_start(self, event: RelationChangedEvent) -> None:
        """Handle the database start in the standby cluster."""
        try:
            if self.charm._patroni.member_started:
                # If the database is started, update the databag in a way the unit is marked as configured
                # for async replication.
                self.charm._peers.data[self.charm.unit].update({"stopped": ""})
                self.charm._peers.data[self.charm.unit].update({
                    "unit-promoted-cluster-counter": self._get_highest_promoted_cluster_counter_value()
                })

                if self.charm.unit.is_leader():
                    # If this unit is the leader, check if all units are ready before making the cluster
                    # active again (including the health checks from the update status hook).
                    if all(
                        self.charm._peers.data[unit].get("unit-promoted-cluster-counter")
                        == self._get_highest_promoted_cluster_counter_value()
                        for unit in {*self.charm._peers.units, self.charm.unit}
                    ):
                        self.charm._peers.data[self.charm.app].update({
                            "cluster_initialised": "True"
                        })
                    elif self._is_following_promoted_cluster():
                        self.charm.unit.status = WaitingStatus(
                            "Waiting for the database to be started in all units"
                        )
                        event.defer()
                        return

                self.charm._set_active_status()
            elif not self.charm.unit.is_leader():
                raise NotReadyError()
            else:
                # If the standby leader fails to start, fix the leader annotation and defer the event.
                self.charm.fix_leader_annotation()
                self.charm.unit.status = WaitingStatus(
                    "Still starting the database in the standby leader"
                )
                event.defer()
        except NotReadyError:
            self.charm.unit.status = WaitingStatus("Waiting for the database to start")
            logger.debug("Deferring on_async_relation_changed: database hasn't started yet.")
            event.defer()

    def handle_read_only_mode(self) -> None:
        """Handle read-only mode (standby cluster that lost the relation with the primary cluster)."""
        promoted_cluster_counter = self.charm._peers.data[self.charm.app].get(
            "promoted-cluster-counter", ""
        )
        if not self.charm.is_blocked or (
            promoted_cluster_counter != "0"
            and self.charm.unit.status.message == READ_ONLY_MODE_BLOCKING_MESSAGE
        ):
            self.charm._set_active_status()
        if (
            promoted_cluster_counter == "0"
            and self.charm.unit.status.message != READ_ONLY_MODE_BLOCKING_MESSAGE
        ):
            self.charm.unit.status = BlockedStatus(READ_ONLY_MODE_BLOCKING_MESSAGE)

    def _is_following_promoted_cluster(self) -> bool:
        """Return True if this unit is following the promoted cluster."""
        if self._get_primary_cluster() is None:
            return False
        return (
            self.charm._peers.data[self.charm.unit].get("unit-promoted-cluster-counter")
            == self._get_highest_promoted_cluster_counter_value()
        )

    def _is_primary_cluster(self) -> bool:
        """Return the primary cluster name."""
        return self.charm.app == self._get_primary_cluster()

    def _on_async_relation_broken(self, _) -> None:
        if "departing" in self.charm._peers.data[self.charm.unit]:
            logger.debug("Early exit on_async_relation_broken: Skipping departing unit.")
            return

        self.charm._peers.data[self.charm.unit].update({
            "stopped": "",
            "unit-promoted-cluster-counter": "",
        })

        # If this is the standby cluster, set 0 in the "promoted-cluster-counter" field to set
        # the cluster in read-only mode message also in the other units.
        if self.charm._patroni.get_standby_leader() is not None:
            if self.charm.unit.is_leader():
                self.charm._peers.data[self.charm.app].update({"promoted-cluster-counter": "0"})
            self.charm.unit.status = BlockedStatus(READ_ONLY_MODE_BLOCKING_MESSAGE)
        else:
            if self.charm.unit.is_leader():
                self.charm._peers.data[self.charm.app].update({"promoted-cluster-counter": ""})
            self.charm.update_config()

    def _on_async_relation_changed(self, event: RelationChangedEvent) -> None:
        """Update the Patroni configuration if one of the clusters was already promoted."""
        primary_cluster = self._get_primary_cluster()
        logger.debug("Primary cluster: %s", primary_cluster)
        if primary_cluster is None:
            logger.debug("Early exit on_async_relation_changed: No primary cluster found.")
            return

        if self._configure_primary_cluster(primary_cluster, event):
            return

        # Return if this is a new unit.
        if not self.charm.unit.is_leader() and self._is_following_promoted_cluster():
            logger.debug("Early exit on_async_relation_changed: following promoted cluster.")
            return

        if not self._stop_database(event):
            return

        if not all(
            "stopped" in self.charm._peers.data[unit]
            or self.charm._peers.data[unit].get("unit-promoted-cluster-counter")
            == self._get_highest_promoted_cluster_counter_value()
            for unit in {*self.charm._peers.units, self.charm.unit}
        ):
            self.charm.unit.status = WaitingStatus(
                "Waiting for the database to be stopped in all units"
            )
            logger.debug("Deferring on_async_relation_changed: not all units stopped.")
            event.defer()
            return

        if self._wait_for_standby_leader(event):
            return

        # Update the asynchronous replication configuration and start the database.
        self.charm.update_config()
        self.container.start(self.charm._postgresql_service)

        self._handle_database_start(event)

    def _on_async_relation_departed(self, event: RelationDepartedEvent) -> None:
        """Set a flag to avoid setting a wrong status message on relation broken event handler."""
        # This is needed because of https://bugs.launchpad.net/juju/+bug/1979811.
        if event.departing_unit == self.charm.unit:
            self.charm._peers.data[self.charm.unit].update({"departing": "True"})

    def _on_async_relation_joined(self, _) -> None:
        """Publish this unit address in the relation data."""
        self._relation.data[self.charm.unit].update({"unit-address": self._get_unit_ip()})

        # Set the counter for new units.
        highest_promoted_cluster_counter = self._get_highest_promoted_cluster_counter_value()
        if highest_promoted_cluster_counter != "0":
            self.charm._peers.data[self.charm.unit].update({
                "unit-promoted-cluster-counter": highest_promoted_cluster_counter
            })

    def _on_promote_cluster(self, event: ActionEvent) -> None:
        """Promote this cluster to the primary cluster."""
        if not self._can_promote_cluster(event):
            return

        relation = self._relation

        # Check if all units from the other cluster  published their pod IPs in the relation data.
        # If not, fail the action telling that all units must publish their pod addresses in the
        # relation data.
        for unit in relation.units:
            if "unit-address" not in relation.data[unit]:
                event.fail(
                    "All units from the other cluster must publish their pod addresses in the relation data."
                )
                return

        system_identifier, error = self.get_system_identifier()
        if error is not None:
            logger.exception(error)
            event.fail("Failed to get system identifier")
            return

        # Increment the current cluster counter in this application side based on the highest counter value.
        promoted_cluster_counter = int(self._get_highest_promoted_cluster_counter_value())
        promoted_cluster_counter += 1
        logger.debug("Promoted cluster counter: %s", promoted_cluster_counter)

        self._update_primary_cluster_data(promoted_cluster_counter, system_identifier)

        # Emit an async replication changed event for this unit (to promote this cluster before demoting the
        # other if this one is a standby cluster, which is needed to correctly setup the async replication
        # when performing a switchover).
        self._re_emit_async_relation_changed_event()

        # Set the status.
        self.charm.unit.status = MaintenanceStatus("Promoting cluster...")

    @property
    def _primary_cluster_endpoint(self) -> str:
        """Return the endpoint from one of the sync-standbys, or from the primary if there is no sync-standby."""
        sync_standby_names = self.charm._patroni.get_sync_standby_names()
        if len(sync_standby_names) > 0:
            unit = self.model.get_unit(sync_standby_names[0])
            return self.charm.get_unit_ip(unit)
        else:
            return self.charm.get_unit_ip(self.charm.unit)

    def _re_emit_async_relation_changed_event(self) -> None:
        """Re-emit the async relation changed event."""
        relation = self._relation
        getattr(self.charm.on, f'{relation.name.replace("-", "_")}_relation_changed').emit(
            relation,
            app=relation.app,
            unit=[unit for unit in relation.units if unit.app == relation.app][0],
        )

    @property
    def _relation(self) -> Relation:
        """Return the relation object."""
        for relation in [
            self.model.get_relation(ASYNC_PRIMARY_RELATION),
            self.model.get_relation(ASYNC_REPLICA_RELATION),
        ]:
            if relation is not None:
                return relation

    def _remove_previous_cluster_information(self) -> None:
        """Remove the previous cluster information."""
        client = Client()
        for values in itertools.product(
            [Endpoints, Service],
            [
                f"patroni-{self.charm._name}",
                f"patroni-{self.charm._name}-config",
                f"patroni-{self.charm._name}-sync",
            ],
        ):
            try:
                client.delete(
                    values[0],
                    name=values[1],
                    namespace=self.charm._namespace,
                )
                logger.debug(f"Deleted {values[0]} {values[1]}")
            except ApiError as e:
                # Ignore the error only when the resource doesn't exist.
                if e.status.code != 404:
                    raise e
                logger.debug(f"{values[0]} {values[1]} not found")

    @property
    def _secret_label(self) -> str:
        """Return the secret label."""
        return f"async-replication-secret-{self._get_highest_promoted_cluster_counter_value()}"

    def _stop_database(self, event: RelationChangedEvent) -> bool:
        """Stop the database."""
        if (
            "stopped" not in self.charm._peers.data[self.charm.unit]
            and not self._is_following_promoted_cluster()
        ):
            if not self.charm.unit.is_leader() and not self.container.exists(POSTGRESQL_DATA_PATH):
                logger.debug("Early exit on_async_relation_changed: following promoted cluster.")
                return False

            self.container.stop(self.charm._postgresql_service)

            if self.charm.unit.is_leader():
                # Remove the "cluster_initialised" flag to avoid self-healing in the update status hook.
                self.charm._peers.data[self.charm.app].update({"cluster_initialised": ""})
                if not self._configure_standby_cluster(event):
                    return False

            # Remove and recreate the pgdata folder to enable replication of the data from the
            # primary cluster.
            logger.info("Removing and recreating pgdata folder")
            self.container.exec(f"rm -r {POSTGRESQL_DATA_PATH}".split()).wait_output()
            self.charm._create_pgdata(self.container)

            self.charm._peers.data[self.charm.unit].update({"stopped": "True"})

        return True

    def update_async_replication_data(self) -> None:
        """Updates the async-replication data, if the unit is the leader.

        This is used to update the standby units with the new primary information.
        """
        relation = self._relation
        if relation is None:
            return
        relation.data[self.charm.unit].update({"unit-address": self._get_unit_ip()})
        if self._is_primary_cluster() and self.charm.unit.is_leader():
            self._update_primary_cluster_data()

    def _update_primary_cluster_data(
        self, promoted_cluster_counter: int = None, system_identifier: str = None
    ) -> None:
        """Update the primary cluster data."""
        async_relation = self._relation

        if promoted_cluster_counter is not None:
            for relation in [async_relation, self.charm._peers]:
                relation.data[self.charm.app].update({
                    "promoted-cluster-counter": str(promoted_cluster_counter)
                })

        primary_cluster_data = {"endpoint": self._primary_cluster_endpoint}

        # Retrieve the secrets that will be shared between the clusters.
        if async_relation.name == ASYNC_PRIMARY_RELATION:
            secret = self._get_secret()
            secret.grant(async_relation)
            primary_cluster_data["secret-id"] = secret.id

        if system_identifier is not None:
            primary_cluster_data["system-id"] = system_identifier

        async_relation.data[self.charm.app]["primary-cluster-data"] = json.dumps(
            primary_cluster_data
        )

    def _wait_for_standby_leader(self, event: RelationChangedEvent) -> bool:
        """Wait for the standby leader to be up and running."""
        try:
            standby_leader = self.charm._patroni.get_standby_leader(check_whether_is_running=True)
        except RetryError:
            standby_leader = None
        if not self.charm.unit.is_leader() and standby_leader is None:
            self.charm.unit.status = WaitingStatus(
                "Waiting for the standby leader start the database"
            )
            logger.debug("Deferring on_async_relation_changed: standby leader hasn't started yet.")
            event.defer()
            return True
        return False
