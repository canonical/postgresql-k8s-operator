#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed Kubernetes Operator for the PostgreSQL database."""
import json
import logging
import secrets
import string
from pathlib import Path
from typing import Iterable, List, Optional

from charms.postgresql.v0.postgresql_helpers import (
    connect_to_database,
    create_database,
    create_user,
)
from lightkube import ApiError, Client, codecs
from lightkube.resources.core_v1 import Pod
from ops.charm import (
    ActionEvent,
    CharmBase,
    LeaderElectedEvent,
    RelationChangedEvent,
    RelationDepartedEvent,
    WorkloadEvent,
)
from ops.main import main
from ops.model import (
    ActiveStatus,
    BlockedStatus,
    MaintenanceStatus,
    ModelError,
    Relation,
    Unit,
    WaitingStatus,
)
from ops.pebble import Layer
from pgconnstr import ConnectionString
from requests import ConnectionError
from tenacity import RetryError

from patroni import NotReadyError, Patroni

logger = logging.getLogger(__name__)

OLD_DB_ADMIN_RELATION = "db-admin"
OLD_DB_RELATION = "db"
PEER = "postgresql-replicas"


class PostgresqlOperatorCharm(CharmBase):
    """Charmed Operator for the PostgreSQL database."""

    def __init__(self, *args):
        super().__init__(*args)

        self._postgresql_service = "postgresql"
        self._unit = self.model.unit.name
        self._name = self.model.app.name
        self._namespace = self.model.name
        self._context = {"namespace": self._namespace, "app_name": self._name}

        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.leader_elected, self._on_leader_elected)
        self.framework.observe(self.on[PEER].relation_changed, self._on_peer_relation_changed)
        self.framework.observe(self.on[PEER].relation_departed, self._on_peer_relation_departed)
        self.framework.observe(
            self.on[OLD_DB_RELATION].relation_changed, self._on_old_relation_changed
        )
        self.framework.observe(
            self.on[OLD_DB_RELATION].relation_departed, self._on_old_db_relation_departed
        )
        self.framework.observe(
            self.on[OLD_DB_ADMIN_RELATION].relation_changed, self._on_old_relation_changed
        )
        self.framework.observe(
            self.on[OLD_DB_ADMIN_RELATION].relation_departed, self._on_old_db_relation_departed
        )
        self.framework.observe(self.on.postgresql_pebble_ready, self._on_postgresql_pebble_ready)
        self.framework.observe(self.on.upgrade_charm, self._on_upgrade_charm)
        self.framework.observe(
            self.on.get_postgres_password_action, self._on_get_postgres_password
        )
        self.framework.observe(self.on.get_primary_action, self._on_get_primary)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self._storage_path = self.meta.storages["pgdata"].location

    def _on_old_relation_changed(self, event: RelationChangedEvent) -> None:
        """Handle the legacy db relation changed event."""
        # Check for some conditions before trying to access the PostgreSQL instance.
        if (
            "cluster_initialised" not in self._peers.data[self.app]
            or not self._patroni.member_started
        ):
            event.defer()
            return

        if not self.unit.is_leader():
            return

        # Get the relation name to handle specific logic for each relation (db and db-admin).
        relation_name = event.relation.name

        self.unit.status = MaintenanceStatus(f"Setting up {relation_name} relation")
        logger.warning(f"DEPRECATION WARNING - `{relation_name}` is a legacy interface")

        unit_relation_databag = event.relation.data[self.unit]
        application_relation_databag = event.relation.data[self.app]

        # When this flag is True it indicates that this hook was already executed and
        # it set the data about database, user, master, standbys, etc. in the relation
        # databag. It's needed to rerun this hook on every relation changed event
        # setting the data again in the databag, otherwise the application charm that
        # is connecting to this database will receive a "database gone" event from the
        # old PostgreSQL library (ops-lib-pgsql) and the connection between the
        # application and this charm will not work.
        already = False
        if application_relation_databag.get("user"):
            already = True

        # Connect to the PostgreSQL instance to later create a user and the database.
        hostname = self._get_hostname_from_unit(self.unit.name.replace("/", "-"))
        connection = connect_to_database(
            "postgres", "postgres", hostname, self._get_postgres_password()
        )
        logger.info(f"Connected to PostgreSQL: {connection}")

        user = (
            # Doesn't generate a username if it was already
            # generated in a previous relation changed event.
            unit_relation_databag["user"]
            if already
            else f"relation_id_{event.relation.id}_{event.app.name.replace('-', '_')}"
        )
        password = unit_relation_databag["password"] if already else self._new_password()
        database = (
            unit_relation_databag["database"]
            if already
            else event.relation.data[event.app].get("database")
        )
        # Sometimes a relation changed event is triggered,
        # and it doesn't have a database name in it.
        if not database:
            logger.warning("No database name provided")
            event.defer()
            return

        # Creates the user and the database for this specific relation if it was not already
        # created in a previous relation changed event.
        if not already:
            # Use the relation name to request or not a superuser (admin flag).
            create_user(connection, user, password, admin=relation_name == OLD_DB_ADMIN_RELATION)
            create_database(connection, database, user)

        connection.close()

        # Get the list of all members in the cluster.
        members = self._patroni.cluster_members
        # Build the primary's connection string.
        primary = str(
            ConnectionString(
                host=f"{self._get_hostname_from_unit(self._patroni.get_primary())}",
                dbname=database,
                port=5432,
                user=user,
                password=password,
                fallback_application_name=event.app.name,
            )
        )
        # Build the standbys' connection strings.
        standbys = ",".join(
            [
                str(
                    ConnectionString(
                        host=f"{self._get_hostname_from_unit(member)}",
                        dbname=database,
                        port=5432,
                        user=user,
                        password=password,
                        fallback_application_name=event.app.name,
                    )
                )
                for member in members
                if self._get_hostname_from_unit(member) != primary
            ]
        )

        # Set the data in both application and unit data bag (it's the logic of the old charm
        # - it needs one more check to confirm whether it's required to do it).
        for databag in [application_relation_databag, unit_relation_databag]:
            # This list of subnets is not being filled correctly yet.
            databag["allowed-subnets"] = self._get_allowed_subnets(event.relation)
            databag["allowed-units"] = self._get_allowed_units(event.relation)
            databag["host"] = f"http://{hostname}"
            databag["master"] = primary
            databag["port"] = "5432"
            databag["standbys"] = standbys
            databag["state"] = "master"
            databag["version"] = "12"
            databag["user"] = user
            databag["password"] = password
            databag["database"] = database

        self.unit.status = ActiveStatus()

    def _get_allowed_units(self, relation: Relation) -> str:
        return ",".join(
            sorted(
                unit.name
                for unit in relation.data
                if isinstance(unit, Unit) and not unit.name.startswith(self.model.app.name)
            )
        )

    def _get_allowed_subnets(self, relation: Relation) -> str:
        def _csplit(s) -> Iterable[str]:
            if s:
                for b in s.split(","):
                    b = b.strip()
                    if b:
                        yield b

        subnets = set()
        for unit, reldata in relation.data.items():
            logger.warning(f"Checking subnets for {unit}")
            logger.warning(reldata)
            if isinstance(unit, Unit) and not unit.name.startswith(self.model.app.name):
                # NB. egress-subnets is not always available.
                subnets.update(set(_csplit(reldata.get("egress-subnets", ""))))
        return ",".join(sorted(subnets))

    def _retrieve_resource(self, resource: str) -> Optional[Path]:
        """Check that the resource exists and return it.

        Returns:
            Path of the resource or None
        """
        try:
            # Fetch the resource path
            return self.model.resources.fetch(resource)
        except (ModelError, NameError):
            return None

    def _store_tls_files(self) -> None:
        """Copy the TLS certificate and key to the PostgreSQL container."""
        # Copy the resources to the storage path if all of them were attached
        # and enable TLS.
        if self._tls_files:
            container = self.unit.get_container("postgresql")

            # Copy the files from the resources' location to the PostgreSQL container.
            for file_path in self._tls_files:
                with open(file_path, "r") as f:
                    container.push(
                        f"{self._storage_path}/{file_path.name}",
                        f,
                        permissions=0o600,
                        user="postgres",
                        group="postgres",
                    )

            # Enable TLS.
            self._patroni.render_patroni_yml_file(True)
            logger.error("TLS enabled")
        else:
            # Disable TLS.
            self._patroni.render_patroni_yml_file()
            logger.error("TLS disabled")

        try:
            if self._patroni.member_started:
                # Make Patroni use the updated configuration.
                self._patroni.reload_patroni_configuration()
        except RetryError:
            # Ignore retry errors that happen when the member has not started yet.
            # The configuration will be loaded correctly when Patroni starts.
            pass

    @property
    def _tls_files(self) -> Optional[List[Path]]:
        """Paths of the TLS certificate and key files.

        Returns:
            A list with the paths of the certificate and the key
                if they were attached as resources to this application.
        """
        resources = ["cert-file", "key-file"]
        return [path for path in map(self._retrieve_resource, resources) if path]

    def _on_old_db_relation_departed(self, event: RelationDepartedEvent) -> None:
        # TODO: implement.
        pass

    def _get_endpoints_to_remove(self) -> List[str]:
        """List the endpoints that were part of the cluster but departed."""
        old = self._endpoints
        current = [self._get_hostname_from_unit(member) for member in self._hosts]
        endpoints_to_remove = list(set(old) - set(current))
        return endpoints_to_remove

    def _on_peer_relation_departed(self, event: RelationDepartedEvent) -> None:
        """The leader removes the departing units from the list of cluster members."""
        if not self.unit.is_leader():
            return

        if "cluster_initialised" not in self._peers.data[self.app]:
            event.defer()
            return

        endpoints_to_remove = self._get_endpoints_to_remove()
        self._remove_from_endpoints(endpoints_to_remove)

    def _on_peer_relation_changed(self, event: RelationChangedEvent) -> None:
        """Reconfigure cluster members."""
        # The cluster must be initialized first in the leader unit
        # before any other member joins the cluster.
        if "cluster_initialised" not in self._peers.data[self.app]:
            event.defer()
            return

        # If the leader is the one receiving the event, it adds the new members,
        # one at a time.
        if self.unit.is_leader():
            self._add_members(event)

        # Don't update this member before it's part of the members list.
        if self._endpoint not in self._endpoints:
            return

        # Update the list of the cluster members in the replicas to make them know each other.
        # Update the cluster members in this unit (updating patroni configuration).
        self._patroni.update_cluster_members()

        # Validate the status of the member before setting an ActiveStatus.
        if not self._patroni.member_started:
            self.unit.status = WaitingStatus("awaiting for member to start")
            event.defer()
            return

        self.unit.status = ActiveStatus()

    def _on_install(self, _) -> None:
        """Event handler for InstallEvent."""
        # Creates custom postgresql.conf file.
        self._patroni.render_postgresql_conf_file()

    def _on_config_changed(self, _) -> None:
        """Handle the config-changed event."""
        # TODO: placeholder method to implement logic specific to configuration change.
        pass

    def _add_members(self, event) -> None:
        """Add new cluster members.

        This method is responsible for adding new members to the cluster
        when new units are added to the application. This event is deferred if
        one of the current units is copying data from the primary, to avoid
        multiple units copying data at the same time, which can cause slow
        transfer rates in these processes and overload the primary instance.
        """
        # Only the leader can reconfigure.
        if not self.unit.is_leader():
            return

        # Reconfiguration can be successful only if the cluster is initialised
        # (i.e. first unit has bootstrap the cluster).
        if "cluster_initialised" not in self._peers.data[self.app]:
            return

        try:
            # Compare set of Patroni cluster members and Juju hosts
            # to avoid the unnecessary reconfiguration.
            if self._patroni.cluster_members == self._hosts:
                return

            logger.info("Reconfiguring cluster")
            self.unit.status = MaintenanceStatus("reconfiguring cluster")
            for member in self._hosts - self._patroni.cluster_members:
                logger.debug("Adding %s to cluster", member)
                self.add_cluster_member(member)
        except NotReadyError:
            logger.info("Deferring reconfigure: another member doing sync right now")
            event.defer()

    def add_cluster_member(self, member: str) -> None:
        """Add member to the cluster if all members are already up and running.

        Raises:
            NotReadyError if either the new member or the current members are not ready.
        """
        hostname = self._get_hostname_from_unit(member)

        if not self._patroni.are_all_members_ready():
            logger.info("not all members are ready")
            raise NotReadyError("not all members are ready")

        # Add the member to the list that should be updated in each other member.
        self._add_to_endpoints(hostname)

        # Add the labels needed for replication in this pod.
        # This also enables the member as part of the cluster.
        try:
            self._patch_pod_labels(member)
        except ApiError as e:
            logger.error("failed to patch pod")
            self.unit.status = BlockedStatus(f"failed to patch pod with error {e}")
            return

    @property
    def _hosts(self) -> set:
        """List of the current Juju hosts.

        Returns:
            a set containing the current Juju hosts
                with the names in the k8s pod name format
        """
        peers = self.model.get_relation(PEER)
        hosts = [self._unit_name_to_pod_name(self.unit.name)] + [
            self._unit_name_to_pod_name(unit.name) for unit in peers.units
        ]
        return set(hosts)

    def _get_hostname_from_unit(self, member: str) -> str:
        """Create a DNS name for a PostgreSQL/Patroni cluster member.

        Args:
            member: the Patroni member name, e.g. "postgresql-k8s-0".

        Returns:
            A string representing the hostname of the PostgreSQL unit.
        """
        unit_id = member.split("-")[-1]
        return f"{self.app.name}-{unit_id}.{self.app.name}-endpoints"

    def _on_leader_elected(self, event: LeaderElectedEvent) -> None:
        """Handle the leader-elected event."""
        data = self._peers.data[self.app]
        postgres_password = data.get("postgres-password", None)
        replication_password = data.get("replication-password", None)

        if postgres_password is None:
            self._peers.data[self.app]["postgres-password"] = self._new_password()

        if replication_password is None:
            self._peers.data[self.app]["replication-password"] = self._new_password()

        # Create resources and add labels needed for replication.
        self._create_resources()

        # Add this unit to the list of cluster members
        # (the cluster should start with only this member).
        if self._endpoint not in self._endpoints:
            self._add_to_endpoints(self._endpoint)

        # Remove departing units when the leader changes.
        self._remove_from_endpoints(self._get_endpoints_to_remove())

        self._add_members(event)

    def _on_postgresql_pebble_ready(self, event: WorkloadEvent) -> None:
        """Event handler for PostgreSQL container on PebbleReadyEvent."""
        # TODO: move this code to an "_update_layer" method in order to also utilize it in
        # config-changed hook.
        # Get the postgresql container so we can configure/manipulate it.
        container = event.workload
        # Create a new config layer.
        new_layer = self._postgresql_layer()

        # Defer the initialization of the workload in the replicas
        # if the cluster hasn't been bootstrap on the primary yet.
        # Otherwise, each unit will create a different cluster and
        # any update in the members list on the units won't have effect
        # on fixing that.
        if not self.unit.is_leader() and "cluster_initialised" not in self._peers.data[self.app]:
            event.defer()
            return

        # Get the current layer.
        current_layer = container.get_plan()
        # Check if there are any changes to layer services.
        if current_layer.services != new_layer.services:
            # Changes were made, add the new layer.
            container.add_layer(self._postgresql_service, new_layer, combine=True)
            logging.info("Added updated layer 'postgresql' to Pebble plan")
            # TODO: move this file generation to on config changed hook
            # when adding configs to this charm.
            self._patroni.render_patroni_yml_file(enable_tls=True if self._tls_files else False)
            # Restart it and report a new status to Juju.
            container.restart(self._postgresql_service)
            logging.info("Restarted postgresql service")

        # Ensure the member is up and running before marking the cluster as initialised.
        if not self._patroni.member_started:
            self.unit.status = WaitingStatus("awaiting for cluster to start")
            event.defer()
            return

        if self.unit.is_leader():
            # Add the labels needed for replication in this pod.
            # This also enables the member as part of the cluster.
            try:
                self._patch_pod_labels(self._unit)
            except ApiError as e:
                logger.error("failed to patch pod")
                self.unit.status = BlockedStatus(f"failed to patch pod with error {e}")
                return

            self._peers.data[self.app]["cluster_initialised"] = "True"

        # All is well, set an ActiveStatus.
        self.unit.status = ActiveStatus()

    def _on_upgrade_charm(self, _) -> None:
        # Add labels required for replication when the pod loses them (like when it's deleted).
        try:
            self._patch_pod_labels(self.unit.name)
        except ApiError as e:
            logger.error("failed to patch pod")
            self.unit.status = BlockedStatus(f"failed to patch pod with error {e}")
            return

        # Tries to store the TLS certificate and key on the PostgreSQL container,
        # as new `juju attach-resource` will trigger this event.
        self._store_tls_files()

    def _patch_pod_labels(self, member: str) -> None:
        """Add labels required for replication to the current pod.

        Args:
            member: name of the unit that needs the labels
        Raises:
            ApiError when there is any problem communicating
                to K8s API
        """
        client = Client()
        patch = {
            "metadata": {"labels": {"application": "patroni", "cluster-name": self._namespace}}
        }
        client.patch(
            Pod,
            name=self._unit_name_to_pod_name(member),
            namespace=self._namespace,
            obj=patch,
        )

    def _create_resources(self) -> None:
        """Create kubernetes resources needed for Patroni."""
        client = Client()
        try:
            with open("src/resources.yaml") as f:
                for resource in codecs.load_all_yaml(f, context=self._context):
                    client.create(resource)
                    logger.info(f"created {str(resource)}")
        except ApiError as e:
            if e.status.code == 409:
                logger.info("replacing resource: %s.", str(resource.to_dict()))
                client.replace(resource)
            else:
                logger.error("failed to create resource: %s.", str(resource.to_dict()))
                self.unit.status = BlockedStatus(f"failed to create services {e}")
                return

    def _on_get_postgres_password(self, event: ActionEvent) -> None:
        """Returns the password for the postgres user as an action response."""
        event.set_results({"postgres-password": self._get_postgres_password()})

    def _on_get_primary(self, event: ActionEvent) -> None:
        """Get primary instance."""
        try:
            primary = self._patroni.get_primary(unit_name_pattern=True)
            event.set_results({"primary": primary})
        except RetryError as e:
            logger.error(f"failed to get primary with error {e}")

    def _on_update_status(self, _) -> None:
        # Until https://github.com/canonical/pebble/issues/6 is fixed,
        # we need to use the logic below to restart the leader
        # and a stuck replica after a failover/switchover.
        try:
            state = self._patroni.get_postgresql_state()
            if state == "restarting":
                # Restart the stuck replica.
                self._restart_postgresql_service()
            elif state == "starting" or state == "stopping":
                # Force a primary change when the current primary is stuck.
                self.force_primary_change()
        except RetryError as e:
            logger.error("failed to check PostgreSQL state")
            self.unit.status = BlockedStatus(f"failed to check PostgreSQL state with error {e}")
            return

        # Display an active status message if the current unit is the primary.
        try:
            if self._patroni.get_primary(unit_name_pattern=True) == self.unit.name:
                self.unit.status = ActiveStatus("Primary")
        except (RetryError, ConnectionError) as e:
            logger.error(f"failed to get primary with error {e}")

    def _restart_postgresql_service(self) -> None:
        """Restart PostgreSQL and Patroni."""
        self.unit.status = MaintenanceStatus(f"restarting {self._postgresql_service} service")
        container = self.unit.get_container("postgresql")
        container.restart(self._postgresql_service)
        self.unit.status = ActiveStatus()

    @property
    def _patroni(self):
        """Returns an instance of the Patroni object."""
        return Patroni(
            self._endpoint,
            self._endpoints,
            self._namespace,
            self._storage_path,
        )

    @property
    def _endpoint(self) -> str:
        """Current unit hostname."""
        return self._get_hostname_from_unit(self._unit_name_to_pod_name(self.unit.name))

    @property
    def _endpoints(self) -> List[str]:
        """Cluster members hostnames."""
        if self._peers:
            return json.loads(self._peers.data[self.app].get("endpoints", "[]"))
        else:
            # If the peer relations was not created yet, return only the current member hostname.
            return [self._endpoint]

    def _add_to_endpoints(self, endpoint) -> None:
        """Add one endpoint to the members list."""
        self._update_endpoints(endpoint_to_add=endpoint)

    def _remove_from_endpoints(self, endpoints: List[str]) -> None:
        """Remove endpoints from the members list."""
        self._update_endpoints(endpoints_to_remove=endpoints)

    def _update_endpoints(
        self, endpoint_to_add: str = None, endpoints_to_remove: List[str] = None
    ) -> None:
        """Update members IPs."""
        # Allow leader to reset which members are part of the cluster.
        if not self.unit.is_leader():
            return

        endpoints = json.loads(self._peers.data[self.app].get("endpoints", "[]"))
        if endpoint_to_add:
            endpoints.append(endpoint_to_add)
        elif endpoints_to_remove:
            for endpoint in endpoints_to_remove:
                endpoints.remove(endpoint)
        self._peers.data[self.app]["endpoints"] = json.dumps(endpoints)

    def _postgresql_layer(self) -> Layer:
        """Returns a Pebble configuration layer for PostgreSQL."""
        pod_name = self._unit_name_to_pod_name(self._unit)
        layer_config = {
            "summary": "postgresql + patroni layer",
            "description": "pebble config layer for postgresql + patroni",
            "services": {
                self._postgresql_service: {
                    "override": "replace",
                    "summary": "entrypoint of the postgresql + patroni image",
                    "command": f"/usr/bin/python3 /usr/local/bin/patroni {self._storage_path}/patroni.yml",
                    "startup": "enabled",
                    "user": "postgres",
                    "group": "postgres",
                    "environment": {
                        "PATRONI_KUBERNETES_LABELS": f"{{application: patroni, cluster-name: {self._name}}}",
                        "PATRONI_KUBERNETES_NAMESPACE": self._namespace,
                        "PATRONI_KUBERNETES_USE_ENDPOINTS": "true",
                        "PATRONI_NAME": pod_name,
                        "PATRONI_SCOPE": self._namespace,
                        "PATRONI_REPLICATION_USERNAME": "replication",
                        "PATRONI_REPLICATION_PASSWORD": self._replication_password,
                        "PATRONI_SUPERUSER_USERNAME": "postgres",
                        "PATRONI_SUPERUSER_PASSWORD": self._get_postgres_password(),
                    },
                }
            },
        }
        return Layer(layer_config)

    def _new_password(self) -> str:
        """Generate a random password string.

        Returns:
           A random password string.
        """
        choices = string.ascii_letters + string.digits
        password = "".join([secrets.choice(choices) for i in range(16)])
        return password

    @property
    def _peers(self) -> Relation:
        """Fetch the peer relation.

        Returns:
             A :class:`ops.model.Relation` object representing
             the peer relation.
        """
        return self.model.get_relation(PEER)

    def _get_postgres_password(self) -> str:
        """Get postgres user password."""
        data = self._peers.data[self.app]
        return data.get("postgres-password", None)

    @property
    def _replication_password(self) -> str:
        """Get replication user password."""
        data = self._peers.data[self.app]
        return data.get("replication-password", None)

    def _unit_name_to_pod_name(self, unit_name: str) -> str:
        """Converts unit name to pod name.

        Args:
            unit_name: name in "postgresql-k8s/0" format.

        Returns:
            pod name in "postgresql-k8s-0" format.
        """
        return unit_name.replace("/", "-")

    def force_primary_change(self) -> None:
        """Force primary changes immediately.

        This function is needed to handle cases related to
        https://github.com/canonical/pebble/issues/6 .
        When a fail-over is started, Patroni gets stuck on the primary
        change because of some zombie process that are not cleaned by Pebble.
        """
        # Change needed to force an immediate failover.
        self._patroni.change_master_start_timeout(0)
        # Restart the stuck previous leader (will trigger an immediate failover).
        self._restart_postgresql_service()
        # Revert configuration to default.
        self._patroni.change_master_start_timeout(None)


if __name__ == "__main__":
    main(PostgresqlOperatorCharm, use_juju_for_storage=True)
