#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed Kubernetes Operator for the PostgreSQL database."""
import json
import logging
from typing import Dict, List, Optional

from charms.postgresql_k8s.v0.postgresql import (
    PostgreSQL,
    PostgreSQLUpdateUserPasswordError,
)
from charms.postgresql_k8s.v0.postgresql_tls import PostgreSQLTLS
from charms.rolling_ops.v0.rollingops import RollingOpsManager
from lightkube import ApiError, Client, codecs
from lightkube.resources.core_v1 import Endpoints, Pod, Service
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
    Container,
    MaintenanceStatus,
    Relation,
    WaitingStatus,
)
from ops.pebble import Layer, PathError, ProtocolError
from requests import ConnectionError
from tenacity import RetryError

from constants import (
    PEER,
    REPLICATION_PASSWORD_KEY,
    REPLICATION_USER,
    REWIND_PASSWORD_KEY,
    SYSTEM_USERS,
    TLS_CA_FILE,
    TLS_CERT_FILE,
    TLS_KEY_FILE,
    USER,
    USER_PASSWORD_KEY,
    WORKLOAD_OS_GROUP,
    WORKLOAD_OS_USER,
)
from patroni import NotReadyError, Patroni
from relations.db import DbProvides
from relations.postgresql_provider import PostgreSQLProvider
from utils import new_password

logger = logging.getLogger(__name__)


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
        self.framework.observe(self.on.get_password_action, self._on_get_password)
        self.framework.observe(self.on.set_password_action, self._on_set_password)
        self.framework.observe(self.on.get_primary_action, self._on_get_primary)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self._storage_path = self.meta.storages["pgdata"].location

        self.postgresql_client_relation = PostgreSQLProvider(self)
        self.legacy_db_relation = DbProvides(self, admin=False)
        self.legacy_db_admin_relation = DbProvides(self, admin=True)
        self.tls = PostgreSQLTLS(self, PEER, [self.primary_endpoint, self.replicas_endpoint])
        self.restart_manager = RollingOpsManager(
            charm=self, relation="restart", callback=self._restart
        )

    @property
    def app_peer_data(self) -> Dict:
        """Application peer relation data object."""
        relation = self.model.get_relation(PEER)
        if relation is None:
            return {}

        return relation.data[self.app]

    @property
    def unit_peer_data(self) -> Dict:
        """Unit peer relation data object."""
        relation = self.model.get_relation(PEER)
        if relation is None:
            return {}

        return relation.data[self.unit]

    def get_secret(self, scope: str, key: str) -> Optional[str]:
        """Get secret from the secret storage."""
        if scope == "unit":
            return self.unit_peer_data.get(key, None)
        elif scope == "app":
            return self.app_peer_data.get(key, None)
        else:
            raise RuntimeError("Unknown secret scope.")

    def set_secret(self, scope: str, key: str, value: Optional[str]) -> None:
        """Get secret from the secret storage."""
        if scope == "unit":
            if not value:
                del self.unit_peer_data[key]
                return
            self.unit_peer_data.update({key: value})
        elif scope == "app":
            if not value:
                del self.app_peer_data[key]
                return
            self.app_peer_data.update({key: value})
        else:
            raise RuntimeError("Unknown secret scope.")

    @property
    def postgresql(self) -> PostgreSQL:
        """Returns an instance of the object used to interact with the database."""
        return PostgreSQL(
            primary_host=self.primary_endpoint,
            current_host=self.endpoint,
            user=USER,
            password=self.get_secret("app", f"{USER}-password"),
            database="postgres",
        )

    @property
    def endpoint(self) -> str:
        """Returns the endpoint of this instance's pod."""
        return f'{self._unit.replace("/", "-")}.{self._build_service_name("endpoints")}'

    @property
    def primary_endpoint(self) -> str:
        """Returns the endpoint of the primary instance's service."""
        return self._build_service_name("primary")

    @property
    def replicas_endpoint(self) -> str:
        """Returns the endpoint of the replicas instances' service."""
        return self._build_service_name("replicas")

    def _build_service_name(self, service: str) -> str:
        """Build a full k8s service name based on the service name."""
        return f"{self._name}-{service}.{self._namespace}.svc.cluster.local"

    def get_hostname_by_unit(self, unit_name: str) -> str:
        """Create a DNS name for a PostgreSQL unit.

        Args:
            unit_name: the juju unit name, e.g. "postgre-sql/1".

        Returns:
            A string representing the hostname of the PostgreSQL unit.
        """
        unit_id = unit_name.split("/")[1]
        return f"{self.app.name}-{unit_id}.{self.app.name}-endpoints"

    def _get_endpoints_to_remove(self) -> List[str]:
        """List the endpoints that were part of the cluster but departed."""
        old = self._endpoints
        current = [self._get_hostname_from_unit(member) for member in self._hosts]
        endpoints_to_remove = list(set(old) - set(current))
        return endpoints_to_remove

    def _on_peer_relation_departed(self, event: RelationDepartedEvent) -> None:
        """The leader removes the departing units from the list of cluster members."""
        # Allow leader to update endpoints if it isn't leaving.
        if not self.unit.is_leader() or event.departing_unit == self.unit:
            return

        if "cluster_initialised" not in self._peers.data[self.app]:
            event.defer()
            return

        endpoints_to_remove = self._get_endpoints_to_remove()
        self.postgresql_client_relation.update_read_only_endpoint()
        self._remove_from_endpoints(endpoints_to_remove)

        # Update the replication configuration.
        self._patroni.render_postgresql_conf_file()
        self._patroni.reload_patroni_configuration()

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
        self.update_config()

        # Validate the status of the member before setting an ActiveStatus.
        if not self._patroni.member_started:
            self.unit.status = WaitingStatus("awaiting for member to start")
            event.defer()
            return

        self.postgresql_client_relation.update_read_only_endpoint()

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
        if self.get_secret("app", USER_PASSWORD_KEY) is None:
            self.set_secret("app", USER_PASSWORD_KEY, new_password())

        if self.get_secret("app", REPLICATION_PASSWORD_KEY) is None:
            self.set_secret("app", REPLICATION_PASSWORD_KEY, new_password())

        if self.get_secret("app", REWIND_PASSWORD_KEY) is None:
            self.set_secret("app", REWIND_PASSWORD_KEY, new_password())

        # Create resources and add labels needed for replication.
        self._create_resources()

        # Add this unit to the list of cluster members
        # (the cluster should start with only this member).
        if self._endpoint not in self._endpoints:
            self._add_to_endpoints(self._endpoint)

        # Remove departing units when the leader changes.
        self._remove_from_endpoints(self._get_endpoints_to_remove())

        self._add_members(event)

        # Update the replication configuration.
        self._patroni.render_postgresql_conf_file()
        try:
            self._patroni.reload_patroni_configuration()
        except RetryError:
            pass  # This error can happen in the first leader election, as Patroni is not running yet.

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

        try:
            self.push_tls_files_to_workload(container)
        except (PathError, ProtocolError) as e:
            logger.error("Cannot push TLS certificates: %r", e)
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

            if not self._patroni.primary_endpoint_ready:
                self.unit.status = WaitingStatus("awaiting for primary endpoint to be ready")
                event.defer()
                return

            self._peers.data[self.app]["cluster_initialised"] = "True"

        # Update the replication configuration.
        self._patroni.render_postgresql_conf_file()
        self._patroni.reload_patroni_configuration()

        # All is well, set an ActiveStatus.
        self.unit.status = ActiveStatus()

    def _on_upgrade_charm(self, _) -> None:
        # Recreate k8s resources and add labels required for replication
        # when the pod loses them (like when it's deleted).
        try:
            self._create_resources()
        except ApiError:
            logger.exception("failed to create k8s resources")
            self.unit.status = BlockedStatus("failed to create k8s resources")
            return

        try:
            self._patch_pod_labels(self.unit.name)
        except ApiError as e:
            logger.error("failed to patch pod")
            self.unit.status = BlockedStatus(f"failed to patch pod with error {e}")
            return

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
            "metadata": {
                "labels": {"application": "patroni", "cluster-name": f"patroni-{self._name}"}
            }
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
                    logger.debug(f"created {str(resource)}")
        except ApiError as e:
            # The 409 error code means that the resource was already created
            # or has a higher version. This can happen if Patroni creates a
            # resource that the charm is expected to create.
            if e.status.code == 409:
                logger.debug("replacing resource: %s.", str(resource.to_dict()))
                client.replace(resource)
            else:
                logger.error("failed to create resource: %s.", str(resource.to_dict()))
                self.unit.status = BlockedStatus(f"failed to create services {e}")
                return

    def _on_get_password(self, event: ActionEvent) -> None:
        """Returns the password for a user as an action response.

        If no user is provided, the password of the operator user is returned.
        """
        username = event.params.get("username", USER)
        if username not in SYSTEM_USERS:
            event.fail(
                f"The action can be run only for users used by the charm or Patroni:"
                f" {', '.join(SYSTEM_USERS)} not {username}"
            )
            return
        event.set_results({f"{username}-password": self.get_secret("app", f"{username}-password")})

    def _on_set_password(self, event: ActionEvent) -> None:
        """Set the password for the specified user."""
        # Only leader can write the new password into peer relation.
        if not self.unit.is_leader():
            event.fail("The action can be run only on leader unit")
            return

        username = event.params.get("username", USER)
        if username not in SYSTEM_USERS:
            event.fail(
                f"The action can be run only for users used by the charm:"
                f" {', '.join(SYSTEM_USERS)} not {username}"
            )
            return

        password = new_password()
        if "password" in event.params:
            password = event.params["password"]

        if password == self.get_secret("app", f"{username}-password"):
            event.log("The old and new passwords are equal.")
            event.set_results({f"{username}-password": password})
            return

        # Ensure all members are ready before trying to reload Patroni
        # configuration to avoid errors (like the API not responding in
        # one instance because PostgreSQL and/or Patroni are not ready).
        if not self._patroni.are_all_members_ready():
            event.fail(
                "Failed changing the password: Not all members healthy or finished initial sync."
            )
            return

        # Update the password in the PostgreSQL instance.
        try:
            self.postgresql.update_user_password(username, password)
        except PostgreSQLUpdateUserPasswordError as e:
            logger.exception(e)
            event.fail(
                "Failed changing the password: Not all members healthy or finished initial sync."
            )
            return

        # Update the password in the secret store.
        self.set_secret("app", f"{username}-password", password)

        # Update and reload Patroni configuration in this unit to use the new password.
        # Other units Patroni configuration will be reloaded in the peer relation changed event.
        self.update_config()

        event.set_results({f"{username}-password": password})

    def _on_get_primary(self, event: ActionEvent) -> None:
        """Get primary instance."""
        try:
            primary = self._patroni.get_primary(unit_name_pattern=True)
            event.set_results({"primary": primary})
        except RetryError as e:
            logger.error(f"failed to get primary with error {e}")

    def _on_stop(self, _) -> None:
        """Remove k8s resources created by the charm and Patroni."""
        client = Client()

        # Get the k8s resources created by the charm.
        with open("src/resources.yaml") as f:
            resources = codecs.load_all_yaml(f, context=self._context)
            # Ignore the service resources, which will be retrieved in the next step.
            resources_to_delete = list(
                filter(
                    lambda x: not isinstance(x, Service),
                    resources,
                )
            )

        # Get the k8s resources created by the charm and Patroni.
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
            except ApiError as e:
                if (
                    e.status.code != 404
                ):  # 404 means that the resource was already deleted by other unit.
                    # Only log a message, as the charm is being stopped.
                    logger.error(f"failed to delete resource: {resource}.")

    def _on_update_status(self, _) -> None:
        """Display an active status message if the current unit is the primary."""
        container = self.unit.get_container("postgresql")
        if not container.can_connect():
            return

        services = container.pebble.get_services(names=[self._postgresql_service])
        if len(services) == 0:
            # Service has not been added nor started yet, so don't try to check Patroni API.
            return

        try:
            if self._patroni.get_primary(unit_name_pattern=True) == self.unit.name:
                self.unit.status = ActiveStatus("Primary")
        except (RetryError, ConnectionError) as e:
            logger.error(f"failed to get primary with error {e}")

    @property
    def _patroni(self):
        """Returns an instance of the Patroni object."""
        return Patroni(
            self._endpoint,
            self._endpoints,
            self.primary_endpoint,
            self._namespace,
            self._storage_path,
            self.get_secret("app", USER_PASSWORD_KEY),
            self.get_secret("app", REPLICATION_PASSWORD_KEY),
            self.get_secret("app", REWIND_PASSWORD_KEY),
            self.postgresql.is_tls_enabled(check_current_host=True),
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
                        "PATRONI_KUBERNETES_LABELS": f"{{application: patroni, cluster-name: patroni-{self._name}}}",
                        "PATRONI_KUBERNETES_NAMESPACE": self._namespace,
                        "PATRONI_KUBERNETES_USE_ENDPOINTS": "true",
                        "PATRONI_NAME": pod_name,
                        "PATRONI_SCOPE": f"patroni-{self._name}",
                        "PATRONI_REPLICATION_USERNAME": REPLICATION_USER,
                        "PATRONI_SUPERUSER_USERNAME": USER,
                    },
                }
            },
        }
        return Layer(layer_config)

    @property
    def _peers(self) -> Relation:
        """Fetch the peer relation.

        Returns:
             A :class:`ops.model.Relation` object representing
             the peer relation.
        """
        return self.model.get_relation(PEER)

    def push_tls_files_to_workload(self, container: Container = None) -> None:
        """Uploads TLS files to the workload container."""
        if container is None:
            container = self.unit.get_container("postgresql")

        key, ca, cert = self.tls.get_tls_files()
        if key is not None:
            container.push(
                f"{self._storage_path}/{TLS_KEY_FILE}",
                key,
                make_dirs=True,
                permissions=0o400,
                user=WORKLOAD_OS_USER,
                group=WORKLOAD_OS_GROUP,
            )
        if ca is not None:
            container.push(
                f"{self._storage_path}/{TLS_CA_FILE}",
                ca,
                make_dirs=True,
                permissions=0o400,
                user=WORKLOAD_OS_USER,
                group=WORKLOAD_OS_GROUP,
            )
        if cert is not None:
            container.push(
                f"{self._storage_path}/{TLS_CERT_FILE}",
                cert,
                make_dirs=True,
                permissions=0o400,
                user=WORKLOAD_OS_USER,
                group=WORKLOAD_OS_GROUP,
            )

        self.update_config()

    def _restart(self, _) -> None:
        """Restart PostgreSQL."""
        try:
            self._patroni.restart_postgresql()
        except RetryError as e:
            logger.error("failed to restart PostgreSQL")
            self.unit.status = BlockedStatus(f"failed to restart PostgreSQL with error {e}")

    def update_config(self) -> None:
        """Updates Patroni config file based on the existence of the TLS files."""
        enable_tls = all(self.tls.get_tls_files())

        # Update and reload configuration based on TLS files availability.
        self._patroni.render_patroni_yml_file(enable_tls=enable_tls)
        if not self._patroni.member_started:
            return

        restart_postgresql = enable_tls != self.postgresql.is_tls_enabled()
        self._patroni.reload_patroni_configuration()

        # Restart PostgreSQL if TLS configuration has changed
        # (so the both old and new connections use the configuration).
        if restart_postgresql:
            self.on[self.restart_manager.name].acquire_lock.emit()

    def _unit_name_to_pod_name(self, unit_name: str) -> str:
        """Converts unit name to pod name.

        Args:
            unit_name: name in "postgresql-k8s/0" format.

        Returns:
            pod name in "postgresql-k8s-0" format.
        """
        return unit_name.replace("/", "-")


if __name__ == "__main__":
    main(PostgresqlOperatorCharm, use_juju_for_storage=True)
