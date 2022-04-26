#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed Kubernetes Operator for the PostgreSQL database."""
import json
import logging
import secrets
import string
from typing import List

from lightkube import ApiError, Client, codecs
from lightkube.resources.core_v1 import Endpoints, Pod, Service
from lightkube.resources.rbac_authorization_v1 import ClusterRole, ClusterRoleBinding
from ops.charm import (
    ActionEvent,
    CharmBase,
    LeaderElectedEvent,
    RelationChangedEvent,
    WorkloadEvent,
)
from ops.main import main
from ops.model import (
    ActiveStatus,
    BlockedStatus,
    MaintenanceStatus,
    Relation,
    WaitingStatus,
)
from ops.pebble import Layer
from requests import ConnectionError
from tenacity import RetryError

from patroni import NotReadyError, Patroni

logger = logging.getLogger(__name__)

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
        self.framework.observe(self.on.postgresql_pebble_ready, self._on_postgresql_pebble_ready)
        self.framework.observe(self.on.stop, self._on_stop)
        self.framework.observe(self.on.upgrade_charm, self._on_upgrade_charm)
        self.framework.observe(
            self.on.get_postgres_password_action, self._on_get_postgres_password
        )
        self.framework.observe(self.on.get_primary_action, self._on_get_primary)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self._storage_path = self.meta.storages["pgdata"].location

    @property
    def _endpoints_to_remove(self):
        """List the endpoints that were part of the cluster but departed."""
        old = self._endpoints
        current = [self._get_hostname_from_unit(member) for member in self._hosts]
        endpoints_to_remove = list(set(old) - set(current))
        return endpoints_to_remove

    def _on_peer_relation_departed(self, _):
        """The leader removes the departing units from the list of cluster members."""
        if not self.unit.is_leader():
            return

        self._update_endpoints(endpoints_to_remove=self._endpoints_to_remove)

    def _on_peer_relation_changed(self, event: RelationChangedEvent):
        """Reconfigure cluster members."""
        # The cluster must be initialized first in the leader unit
        # before any other member joins the cluster.
        if "cluster_initialised" not in self._peers.data[self.app]:
            event.defer()
            return

        # If the leader is the one receiving the event, it adds the new members,
        # one at a time.
        if self.unit.is_leader():
            self._reconfigure(event)
            return

        if self._endpoint in self._endpoints:
            # Update the list of the cluster members in the replicas to make them know each other.
            try:
                # Update the cluster members in this unit (updating patroni configuration).
                self._patroni.update_cluster_members()
                # Add the labels needed for replication in this pod.
                self._patch_pod_labels(self._unit)
                # Validate the status of the member before setting an ActiveStatus.
                try:
                    if not self._patroni.member_started:
                        raise NotReadyError
                except (NotReadyError, RetryError):
                    self.unit.status = WaitingStatus("awaiting for member to start")
                    event.defer()
                    return
                self.unit.status = ActiveStatus()
            except RetryError:
                self.unit.status = BlockedStatus("failed to update cluster members on member")

    def _on_install(self, _) -> None:
        """Event handler for InstallEvent."""
        # Creates custom postgresql.conf file.
        self._patroni.render_postgresql_conf_file()

    def _on_config_changed(self, _):
        """Handle the config-changed event."""
        # TODO: placeholder method to implement logic specific to configuration change.
        pass

    def _reconfigure(self, event) -> None:
        """Reconfigure cluster members."""
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
            for member in self._hosts - self._patroni.cluster_members:
                logger.debug("Adding %s to cluster", member)
                self.add_cluster_member(member)
        except NotReadyError:
            logger.info("Deferring reconfigure: another member doing sync right now")
            event.defer()

    def add_cluster_member(self, member: str):
        """Add member to the cluster if all members are already up and running.

        Raises:
            NotReadyError if either the new member or the current members are not ready.
        """
        hostname = self._get_hostname_from_unit(member)

        if not self._patroni.is_all_members_ready():
            logger.info("not all members are ready")
            raise NotReadyError("not all members are ready")

        # Add the member to the list that should be updated in each other member.
        self._update_endpoints(endpoint_to_add=hostname)
        # Add the required labels for replication to the member pod.
        self._patch_pod_labels(member)

        # Update the list of members in this unit (updating the Patroni configuration).
        try:
            self._patroni.update_cluster_members()
        except RetryError:
            self.unit.status = BlockedStatus("failed to update cluster members on member")

    @property
    def _hosts(self) -> set:
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
        unit_id = member.split("-")[2]
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

        # Patch the pod labels of this unit to enable replication later.
        self._patch_pod_labels(self.unit.name)

        # Add this unit to the list of cluster members
        # (the cluster should start with only this member).
        if self._endpoint not in self._endpoints:
            self._update_endpoints(endpoint_to_add=self._endpoint)

        # Remove departing units when the leader changes.
        if self._endpoints_to_remove:
            self._update_endpoints(endpoints_to_remove=self._endpoints_to_remove)

        self._reconfigure(event)

    def _on_postgresql_pebble_ready(self, event: WorkloadEvent) -> None:
        """Event handler for PostgreSQL container on PebbleReadyEvent."""
        # TODO: move this code to an "_update_layer" method in order to also utilize it in
        # config-changed hook.
        # Get the postgresql container so we can configure/manipulate it.
        container = event.workload
        # Create a new config layer.
        new_layer = self._postgresql_layer()

        # Defer the event if we pebble is not available yet.
        if not container.can_connect():
            self.unit.status = WaitingStatus("waiting for Pebble in workload container")
            event.defer()
            return

        # Defer the initialization of the workload in the replicas
        # if the cluster hasn't been bootstrap on the primary yet.
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
            self._patroni.render_patroni_yml_file()
            # Restart it and report a new status to Juju.
            container.restart(self._postgresql_service)
            logging.info("Restarted postgresql service")

        # Ensure the member is up and running before marking the cluster as initialised.
        try:
            if not self._patroni.member_started:
                raise NotReadyError
        except (NotReadyError, RetryError):
            self.unit.status = WaitingStatus("awaiting for cluster to start")
            event.defer()
            return

        if self.unit.is_leader():
            self._peers.data[self.app]["cluster_initialised"] = "True"

        # All is well, set an ActiveStatus.
        self.unit.status = ActiveStatus()

    def _on_upgrade_charm(self, _) -> None:
        # Add labels required for replication when the pod loses them (like when it's deleted).
        self._patch_pod_labels(self.unit.name)

    def _patch_pod_labels(self, member: str) -> None:
        """Add labels required for replication to the current pod."""
        try:
            client = Client()
            patch = {
                "metadata": {"labels": {"application": "patroni", "cluster-name": self._namespace}}
            }
            client.patch(
                Pod,
                # name=self._unit_name_to_pod_name(self._unit),
                name=self._unit_name_to_pod_name(member),
                namespace=self._namespace,
                obj=patch,
            )
        except ApiError as e:
            logger.error("failed to patch pod")
            self.unit.status = BlockedStatus(f"failed to patch pod with error {e}")

    def _create_resources(self):
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

    def _on_stop(self, _) -> None:
        """Handle the stop event."""
        # Check to run the teardown actions only once.
        if not self.unit.is_leader():
            return

        client = Client()
        resources_to_delete = []

        # Get the k8s resources created by the charm.
        with open("src/resources.yaml") as f:
            resources = codecs.load_all_yaml(f, context=self._context)
            # Ignore the cluster role and its binding that were created together with the
            # application and also the service resources, which will be retrieved in the next step.
            resources_to_delete.extend(
                list(
                    filter(
                        lambda x: not isinstance(x, (ClusterRole, ClusterRoleBinding, Service)),
                        resources,
                    )
                )
            )

        # Get the k8s resources created by Patroni.
        for kind in [Endpoints, Service]:
            resources_to_delete.extend(
                client.list(
                    kind,
                    namespace=self._namespace,
                    labels={"app.juju.is/created-by": f"{self._name}"},
                )
            )

        # Delete the resources.
        for resource in resources_to_delete:
            try:
                client.delete(
                    type(resource),
                    name=resource.metadata.name,
                    namespace=resource.metadata.namespace,
                )
            except ApiError:
                # Only log a message, as the charm is being stopped.
                logger.error(f"failed to delete resource: {resource}.")

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
            self._unit_ip,
            self._storage_path,
        )

    @property
    def _unit_ip(self) -> str:
        """Current unit ip."""
        return str(self.model.get_binding(PEER).network.bind_address)

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

    def force_primary_change(self):
        """Force primary changes immediately.

        This function is needed to handle cases related to
        https://github.com/canonical/pebble/issues/6.
        """
        # Change needed to force an immediate failover.
        self._patroni.change_master_start_timeout(0)
        # Restart the stuck previous leader (will trigger an immediate failover).
        self._restart_postgresql_service()
        # Revert configuration to default.
        self._patroni.change_master_start_timeout(None)


if __name__ == "__main__":
    main(PostgresqlOperatorCharm, use_juju_for_storage=True)
