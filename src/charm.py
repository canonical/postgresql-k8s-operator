#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed Kubernetes Operator for the PostgreSQL database."""
import json
import logging
from typing import Dict, List, Optional

from charms.observability_libs.v1.kubernetes_service_patch import KubernetesServicePatch
from charms.postgresql_k8s.v0.postgresql import (
    PostgreSQL,
    PostgreSQLUpdateUserPasswordError,
)
from charms.postgresql_k8s.v0.postgresql_tls import PostgreSQLTLS
from charms.rolling_ops.v0.rollingops import RollingOpsManager, RunWithLock
from lightkube import ApiError, Client
from lightkube.models.core_v1 import ServicePort, ServiceSpec
from lightkube.models.meta_v1 import ObjectMeta
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
from ops.pebble import ChangeError, Layer, PathError, ProtocolError, ServiceStatus
from requests import ConnectionError
from tenacity import RetryError

from backups import PostgreSQLBackups
from constants import (
    BACKUP_USER,
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
        self.pgbackrest_server_service = "pgbackrest server"
        self._unit = self.model.unit.name
        self._name = self.model.app.name
        self._namespace = self.model.name
        self._context = {"namespace": self._namespace, "app_name": self._name}
        self.cluster_name = f"patroni-{self._name}"

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
        self.backup = PostgreSQLBackups(self, "s3-parameters")
        self.tls = PostgreSQLTLS(self, PEER, [self.primary_endpoint, self.replicas_endpoint])
        self.restart_manager = RollingOpsManager(
            charm=self, relation="restart", callback=self._restart
        )

        postgresql_db_port = ServicePort(5432, name="database")
        patroni_api_port = ServicePort(8008, name="api")
        self.service_patcher = KubernetesServicePatch(self, [postgresql_db_port, patroni_api_port])

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
            logger.debug(
                "Deferring on_peer_relation_departed: Cluster must be initialized before members can leave"
            )
            event.defer()
            return

        endpoints_to_remove = self._get_endpoints_to_remove()
        self.postgresql_client_relation.update_read_only_endpoint()
        self._remove_from_endpoints(endpoints_to_remove)

    def _on_peer_relation_changed(self, event: RelationChangedEvent) -> None:
        """Reconfigure cluster members."""
        # The cluster must be initialized first in the leader unit
        # before any other member joins the cluster.
        if "cluster_initialised" not in self._peers.data[self.app]:
            logger.debug(
                "Deferring on_peer_relation_changed: Cluster must be initialized before members can join"
            )
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
        container = self.unit.get_container("postgresql")
        if not container.can_connect():
            logger.debug(
                "Early exit on_peer_relation_changed: Waiting for container to become available"
            )
            return
        self.update_config()

        # Validate the status of the member before setting an ActiveStatus.
        if not self._patroni.member_started:
            logger.debug("Deferring on_peer_relation_changed: Waiting for member to start")
            self.unit.status = WaitingStatus("awaiting for member to start")
            event.defer()
            return

        self.postgresql_client_relation.update_read_only_endpoint()

        # Start or stop the pgBackRest TLS server service when TLS certificate change.
        if not self.backup.start_stop_pgbackrest_service():
            # Ping primary to start its TLS server.
            self.unit_peer_data.update({"start-tls-server": "True"})
            logger.debug(
                "Deferring on_peer_relation_changed: awaiting for TLS server service to start on primary"
            )
            event.defer()
            return
        else:
            self.unit_peer_data.pop("start-tls.server", None)

        if not self.is_blocked:
            self.unit.status = ActiveStatus()

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
        try:
            self._create_services()
        except ApiError:
            logger.exception("failed to create k8s services")
            self.unit.status = BlockedStatus("failed to create k8s services")
            return

        # Add this unit to the list of cluster members
        # (the cluster should start with only this member).
        if self._endpoint not in self._endpoints:
            self._add_to_endpoints(self._endpoint)

        # Remove departing units when the leader changes.
        self._remove_from_endpoints(self._get_endpoints_to_remove())

        self._add_members(event)

    def _create_pgdata(self, container: Container):
        """Create the PostgreSQL data directory."""
        path = f"{self._storage_path}/pgdata"
        if not container.exists(path):
            container.make_dir(
                path, permissions=0o770, user=WORKLOAD_OS_USER, group=WORKLOAD_OS_GROUP
            )

    def _on_postgresql_pebble_ready(self, event: WorkloadEvent) -> None:
        """Event handler for PostgreSQL container on PebbleReadyEvent."""
        # TODO: move this code to an "_update_layer" method in order to also utilize it in
        # config-changed hook.
        # Get the postgresql container so we can configure/manipulate it.
        container = event.workload

        # Create the PostgreSQL data directory. This is needed on cloud environments
        # where the volume is mounted with more restrictive permissions.
        self._create_pgdata(container)

        self.unit.set_workload_version(self._patroni.rock_postgresql_version)

        # Defer the initialization of the workload in the replicas
        # if the cluster hasn't been bootstrap on the primary yet.
        # Otherwise, each unit will create a different cluster and
        # any update in the members list on the units won't have effect
        # on fixing that.
        if not self.unit.is_leader() and "cluster_initialised" not in self._peers.data[self.app]:
            logger.debug(
                "Deferring on_postgresql_pebble_ready: Not leader and cluster not initialized"
            )
            event.defer()
            return

        try:
            self.push_tls_files_to_workload(container)
        except (PathError, ProtocolError) as e:
            logger.error(
                "Deferring on_postgresql_pebble_ready: Cannot push TLS certificates: %r", e
            )
            event.defer()
            return

        # Start the database service.
        self._update_pebble_layers()

        # Ensure the member is up and running before marking the cluster as initialised.
        if not self._patroni.member_started:
            logger.debug("Deferring on_postgresql_pebble_ready: Waiting for cluster to start")
            self.unit.status = WaitingStatus("awaiting for cluster to start")
            event.defer()
            return

        if self.unit.is_leader() and not self._initialize_cluster(event):
            return

        # Update the archive command and replication configurations.
        self.update_config()

        # All is well, set an ActiveStatus.
        self.unit.status = ActiveStatus()

    def _initialize_cluster(self, event: WorkloadEvent) -> bool:
        # Add the labels needed for replication in this pod.
        # This also enables the member as part of the cluster.
        try:
            self._patch_pod_labels(self._unit)
        except ApiError as e:
            logger.error("failed to patch pod")
            self.unit.status = BlockedStatus(f"failed to patch pod with error {e}")
            return False

        # Create resources and add labels needed for replication
        try:
            self._create_services()
        except ApiError:
            logger.exception("failed to create k8s services")
            self.unit.status = BlockedStatus("failed to create k8s services")
            return False

        if not self._patroni.primary_endpoint_ready:
            logger.debug(
                "Deferring on_postgresql_pebble_ready: Waiting for primary endpoint to be ready"
            )
            self.unit.status = WaitingStatus("awaiting for primary endpoint to be ready")
            event.defer()
            return False

        # Create the backup user.
        if BACKUP_USER not in self.postgresql.list_users():
            self.postgresql.create_user(BACKUP_USER, new_password(), admin=True)

        # Mark the cluster as initialised.
        self._peers.data[self.app]["cluster_initialised"] = "True"

        return True

    @property
    def is_blocked(self) -> bool:
        """Returns whether the unit is in a blocked state."""
        return isinstance(self.unit.status, BlockedStatus)

    def _on_upgrade_charm(self, _) -> None:
        # Recreate k8s resources and add labels required for replication
        # when the pod loses them (like when it's deleted).
        try:
            self._create_services()
        except ApiError:
            logger.exception("failed to create k8s services")
            self.unit.status = BlockedStatus("failed to create k8s services")
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
            "metadata": {"labels": {"application": "patroni", "cluster-name": self.cluster_name}}
        }
        client.patch(
            Pod,
            name=self._unit_name_to_pod_name(member),
            namespace=self._namespace,
            obj=patch,
        )

    def _create_services(self) -> None:
        """Create kubernetes services for primary and replicas endpoints."""
        client = Client()

        pod0 = client.get(
            res=Pod,
            name=f"{self.app.name}-0",
            namespace=self.model.name,
        )

        services = {
            "primary": "master",
            "replicas": "replica",
        }
        for service_name_suffix, role_selector in services.items():
            service = Service(
                metadata=ObjectMeta(
                    name=f"{self._name}-{service_name_suffix}",
                    namespace=self.model.name,
                    ownerReferences=pod0.metadata.ownerReferences,
                    labels={
                        "app.kubernetes.io/name": self.app.name,
                    },
                ),
                spec=ServiceSpec(
                    ports=[
                        ServicePort(
                            name="api",
                            port=8008,
                            targetPort=8008,
                        ),
                        ServicePort(
                            name="database",
                            port=5432,
                            targetPort=5432,
                        ),
                    ],
                    selector={
                        "app.kubernetes.io/name": self.app.name,
                        "cluster-name": f"patroni-{self.app.name}",
                        "role": role_selector,
                    },
                ),
            )
            client.apply(
                obj=service,
                name=service.metadata.name,
                namespace=service.metadata.namespace,
                force=True,
                field_manager=self.model.app.name,
            )

    @property
    def _has_blocked_status(self) -> bool:
        """Returns whether the unit is in a blocked state."""
        return isinstance(self.unit.status, BlockedStatus)

    @property
    def _has_waiting_status(self) -> bool:
        """Returns whether the unit is in a waiting state."""
        return isinstance(self.unit.status, WaitingStatus)

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
        event.set_results({"password": self.get_secret("app", f"{username}-password")})

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
            event.set_results({"password": password})
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

        event.set_results({"password": password})

    def _on_get_primary(self, event: ActionEvent) -> None:
        """Get primary instance."""
        try:
            primary = self._patroni.get_primary(unit_name_pattern=True)
            event.set_results({"primary": primary})
        except RetryError as e:
            logger.error(f"failed to get primary with error {e}")

    def _on_stop(self, _):
        # Patch the services to remove them when the StatefulSet is deleted
        # (i.e. application is removed).
        try:
            client = Client(field_manager=self.model.app.name)

            pod0 = client.get(
                res=Pod,
                name=f"{self.app.name}-0",
                namespace=self.model.name,
            )
        except ApiError:
            # Only log the exception.
            logger.exception("failed to get first pod info")
            return

        try:
            # Get the k8s resources created by the charm and Patroni.
            resources_to_patch = []
            for kind in [Endpoints, Service]:
                resources_to_patch.extend(
                    client.list(
                        kind,
                        namespace=self._namespace,
                        labels={"app.juju.is/created-by": f"{self._name}"},
                    )
                )
        except ApiError:
            # Only log the exception.
            logger.exception("failed to get the k8s resources created by the charm and Patroni")
            return

        for resource in resources_to_patch:
            # Ignore resources created by Juju or the charm
            # (which are already patched).
            if (
                type(resource) == Service
                and resource.metadata.name
                in [
                    self._name,
                    f"{self._name}-endpoints",
                    f"{self._name}-primary",
                    f"{self._name}-replicas",
                ]
            ) or resource.metadata.ownerReferences == pod0.metadata.ownerReferences:
                continue
            # Patch the resource.
            try:
                resource.metadata.ownerReferences = pod0.metadata.ownerReferences
                resource.metadata.managedFields = None
                client.apply(
                    obj=resource,
                    name=resource.metadata.name,
                    namespace=resource.metadata.namespace,
                    force=True,
                )
            except ApiError:
                # Only log the exception.
                logger.exception(
                    f"failed to patch k8s {type(resource).__name__} {resource.metadata.name}"
                )

    def _on_update_status(self, _) -> None:
        """Update the unit status message."""
        container = self.unit.get_container("postgresql")
        if not container.can_connect():
            logger.debug("on_update_status early exit: Cannot connect to container")
            return

        if self._has_blocked_status or self._has_waiting_status:
            logger.debug("on_update_status early exit: Unit is in Blocked/Waiting status")
            return

        services = container.pebble.get_services(names=[self._postgresql_service])
        if len(services) == 0:
            # Service has not been added nor started yet, so don't try to check Patroni API.
            logger.debug("on_update_status early exit: Service has not been added nor started yet")
            return

        if "restoring-backup" in self.app_peer_data:
            if services[0].current != ServiceStatus.ACTIVE:
                self.unit.status = BlockedStatus("Failed to restore backup")
                return

            if not self._patroni.member_started:
                logger.debug("on_update_status early exit: Patroni has not started yet")
                return

            # Remove the restoring backup flag and the restore stanza name.
            self.app_peer_data.update({"restoring-backup": "", "restore-stanza": ""})
            self.update_config()

            can_use_s3_repository, validation_message = self.backup.can_use_s3_repository()
            if not can_use_s3_repository:
                self.unit.status = BlockedStatus(validation_message)
                return

        if self._handle_processes_failures():
            return

        self._set_primary_status_message()

    def _handle_processes_failures(self) -> bool:
        """Handle Patroni and PostgreSQL OS processes failures.

        Returns:
            a bool indicating whether the charm performed any action.
        """
        container = self.unit.get_container("postgresql")

        # Restart the Patroni process if it was killed (in that case, the PostgreSQL
        # process is still running). This is needed until
        # https://github.com/canonical/pebble/issues/149 is resolved.
        if not self._patroni.member_started and self._patroni.is_database_running:
            try:
                container.restart(self._postgresql_service)
                logger.info("restarted Patroni because it was not running")
            except ChangeError:
                logger.error("failed to restart Patroni after checking that it was not running")
                return False
            return True

        return False

    def _set_primary_status_message(self) -> None:
        """Display 'Primary' in the unit status message if the current unit is the primary."""
        try:
            if self._patroni.get_primary(unit_name_pattern=True) == self.unit.name:
                self.unit.status = ActiveStatus("Primary")
            elif self._patroni.member_started:
                self.unit.status = ActiveStatus()
        except (RetryError, ConnectionError) as e:
            logger.error(f"failed to get primary with error {e}")

    @property
    def _patroni(self):
        """Returns an instance of the Patroni object."""
        return Patroni(
            self,
            self._endpoint,
            self._endpoints,
            self.primary_endpoint,
            self._namespace,
            self._storage_path,
            self.get_secret("app", USER_PASSWORD_KEY),
            self.get_secret("app", REPLICATION_PASSWORD_KEY),
            self.get_secret("app", REWIND_PASSWORD_KEY),
            bool(self.unit_peer_data.get("tls")),
        )

    @property
    def is_primary(self) -> bool:
        """Return whether this unit is the primary instance."""
        return self._unit == self._patroni.get_primary(unit_name_pattern=True)

    @property
    def is_tls_enabled(self) -> bool:
        """Return whether TLS is enabled."""
        return all(self.tls.get_tls_files())

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

    @property
    def peer_members_endpoints(self) -> List[str]:
        """Fetch current list of peer members endpoints.

        Returns:
            A list of peer members addresses (strings).
        """
        # Get all members endpoints and remove the current unit endpoint from the list.
        endpoints = self._endpoints
        current_unit_endpoint = self._get_hostname_from_unit(
            self._unit_name_to_pod_name(self._unit)
        )
        if current_unit_endpoint in endpoints:
            endpoints.remove(current_unit_endpoint)
        return endpoints

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
                    "command": f"patroni {self._storage_path}/patroni.yml",
                    "startup": "enabled",
                    "user": WORKLOAD_OS_USER,
                    "group": WORKLOAD_OS_GROUP,
                    "environment": {
                        "PATRONI_KUBERNETES_LABELS": f"{{application: patroni, cluster-name: {self.cluster_name}}}",
                        "PATRONI_KUBERNETES_NAMESPACE": self._namespace,
                        "PATRONI_KUBERNETES_USE_ENDPOINTS": "true",
                        "PATRONI_NAME": pod_name,
                        "PATRONI_SCOPE": self.cluster_name,
                        "PATRONI_REPLICATION_USERNAME": REPLICATION_USER,
                        "PATRONI_SUPERUSER_USERNAME": USER,
                    },
                },
                self.pgbackrest_server_service: {
                    "override": "replace",
                    "summary": "pgBackRest server",
                    "command": self.pgbackrest_server_service,
                    "startup": "disabled",
                    "user": WORKLOAD_OS_USER,
                    "group": WORKLOAD_OS_GROUP,
                },
            },
            "checks": {
                self._postgresql_service: {
                    "override": "replace",
                    "level": "ready",
                    "http": {
                        "url": f"{self._patroni._patroni_url}/health",
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
            container.push(
                "/usr/local/share/ca-certificates/ca.crt",
                ca,
                make_dirs=True,
                permissions=0o400,
                user=WORKLOAD_OS_USER,
                group=WORKLOAD_OS_GROUP,
            )
            container.exec(["update-ca-certificates"]).wait()
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

    def _restart(self, event: RunWithLock) -> None:
        """Restart PostgreSQL."""
        if not self._patroni.are_all_members_ready():
            logger.debug("Early exit _restart: not all members ready yet")
            event.defer()
            return

        try:
            self._patroni.restart_postgresql()
        except RetryError:
            error_message = "failed to restart PostgreSQL"
            logger.exception(error_message)
            self.unit.status = BlockedStatus(error_message)
            return

        # Update health check URL.
        self._update_pebble_layers()

        # Start or stop the pgBackRest TLS server service when TLS certificate change.
        self.backup.start_stop_pgbackrest_service()

    def update_config(self) -> None:
        """Updates Patroni config file based on the existence of the TLS files."""
        enable_tls = all(self.tls.get_tls_files())

        # Update and reload configuration based on TLS files availability.
        self._patroni.render_patroni_yml_file(
            connectivity=self.unit_peer_data.get("connectivity", "on") == "on",
            enable_tls=enable_tls,
            backup_id=self.app_peer_data.get("restoring-backup"),
            stanza=self.app_peer_data.get("stanza"),
            restore_stanza=self.app_peer_data.get("restore-stanza"),
        )
        if not self._patroni.member_started:
            # If Patroni/PostgreSQL has not started yet and TLS relations was initialised,
            # then mark TLS as enabled. This commonly happens when the charm is deployed
            # in a bundle together with the TLS certificates operator. This flag is used to
            # know when to call the Patroni API using HTTP or HTTPS.
            self.unit_peer_data.update({"tls": "enabled" if enable_tls else ""})
            logger.debug("Early exit update_config: Patroni not started yet")
            return

        restart_postgresql = enable_tls != self.postgresql.is_tls_enabled()
        self._patroni.reload_patroni_configuration()
        self.unit_peer_data.update({"tls": "enabled" if enable_tls else ""})

        # Restart PostgreSQL if TLS configuration has changed
        # (so the both old and new connections use the configuration).
        if restart_postgresql:
            self.on[self.restart_manager.name].acquire_lock.emit()

    def _update_pebble_layers(self) -> None:
        """Update the pebble layers to keep the health check URL up-to-date."""
        container = self.unit.get_container("postgresql")

        # Get the current layer.
        current_layer = container.get_plan()

        # Create a new config layer.
        new_layer = self._postgresql_layer()

        # Check if there are any changes to layer services.
        if current_layer.services != new_layer.services:
            # Changes were made, add the new layer.
            container.add_layer(self._postgresql_service, new_layer, combine=True)
            logging.info("Added updated layer 'postgresql' to Pebble plan")
            container.restart(self._postgresql_service)
            logging.info("Restarted postgresql service")
        if current_layer.checks != new_layer.checks:
            # Changes were made, add the new layer.
            container.add_layer(self._postgresql_service, new_layer, combine=True)
            logging.info("Updated health checks")

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
