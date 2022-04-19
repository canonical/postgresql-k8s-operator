#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed Kubernetes Operator for the PostgreSQL database."""

import logging
import secrets
import string

from charms.postgresql.v0 import postgresql_helpers
from charms.postgresql.v0.postgresql import (
    PostgreSQLEvents,
    PostgreSQLProvides,
    PostgreSQLRelationEvent,
    ProxyAuthDetailsRequestedEvent,
)
from lightkube import ApiError, Client, codecs
from lightkube.resources.core_v1 import Pod
from ops.charm import ActionEvent, CharmBase, WorkloadEvent
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

from patroni import Patroni

logger = logging.getLogger(__name__)

DATABASE_RELATION = "database"
PEER = "postgresql-replicas"


# class RoleChangeEvent(EventBase):
#     """A custom event for role changes."""
#     pass
#
# class CustomEventCharmEvents(CharmEvents):
#     role_change = EventSource(RoleChangeEvent)


class PostgresqlOperatorCharm(CharmBase):
    """Charmed Operator for the PostgreSQL database."""

    # on = CustomEventCharmEvents()

    on = PostgreSQLEvents()

    def __init__(self, *args):
        super().__init__(*args)

        self._postgresql_service = "postgresql"
        self._unit = self.model.unit.name
        self._name = self.model.app.name
        self._namespace = self.model.name
        self._context = {"namespace": self._namespace, "app_name": self._name}
        self.postgresql_provides = PostgreSQLProvides(self, "database", "proxy")

        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.leader_elected, self._on_leader_elected)
        self.framework.observe(self.on.postgresql_pebble_ready, self._on_postgresql_pebble_ready)
        self.framework.observe(self.on.database_requested, self._on_relation_changed_handler)
        self.framework.observe(
            self.on.proxy_auth_details_requested, self._on_relation_changed_handler
        )
        # self.framework.observe(self.on.role_change, self._on_role_change)
        self.framework.observe(self.on.upgrade_charm, self._on_upgrade_charm)
        self.framework.observe(
            self.on.get_postgres_password_action, self._on_get_postgres_password
        )
        self.framework.observe(self.on.get_primary_action, self._on_get_primary)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self._storage_path = self.meta.storages["pgdata"].location
        self._patroni = Patroni(self._pod_ip, self._storage_path)

    def _on_install(self, _) -> None:
        """Event handler for InstallEvent."""
        # Create resources and add labels needed for replication.
        self._create_resources()
        self._patch_pod_labels()
        # Creates custom postgresql.conf file.
        self._patroni.render_postgresql_conf_file()

    def _on_config_changed(self, _):
        """Handle the config-changed event."""
        # TODO: placeholder method to implement logic specific to configuration change.
        pass

    def _on_leader_elected(self, _) -> None:
        """Handle the leader-elected event."""
        data = self._peers.data[self.app]
        postgres_password = data.get("postgres-password", None)
        replication_password = data.get("replication-password", None)

        if postgres_password is None:
            self._peers.data[self.app]["postgres-password"] = self._new_password()

        if replication_password is None:
            self._peers.data[self.app]["replication-password"] = self._new_password()

    def _on_postgresql_pebble_ready(self, event: WorkloadEvent) -> None:
        """Event handler for PostgreSQL container on PebbleReadyEvent."""
        # TODO: move this code to an "_update_layer" method in order to also utilize it in
        # config-changed hook.
        # Get the postgresql container so we can configure/manipulate it.
        container = event.workload
        # Create a new config layer.
        new_layer = self._postgresql_layer()

        if container.can_connect():
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
            # All is well, set an ActiveStatus.
            self.unit.status = ActiveStatus()
        else:
            self.unit.status = WaitingStatus("waiting for Pebble in workload container")

    def _on_relation_changed_handler(self, event: PostgreSQLRelationEvent) -> None:
        # Only the leader should create objects in the database to avoid duplications.
        if not self.unit.is_leader():
            return

        try:
            primary_ready = self._patroni.is_primary_ready()
        except RetryError as e:
            logger.error(f"failed to get primary status with error {e}")
            event.defer()
            return

        if not primary_ready:
            logger.info("primary not ready yet")
            event.defer()
            return

        connection = postgresql_helpers.connect_to_database(
            "postgres", "postgres", self._primary_endpoint, self._get_postgres_password()
        )

        # Get the name of the database from the relation.
        database = event.database
        if postgresql_helpers.database_exists(connection, database):
            # TODO: check how to handle this situation the best way.
            logger.warning(f"database {database} already exists")
            return

        # Flag used to check if the created user needs more privileges.
        is_proxy_relation = isinstance(event, ProxyAuthDetailsRequestedEvent)

        user = postgresql_helpers.build_username(event.relation.app.name, is_proxy_relation)
        user_password = self._new_password()

        postgresql_helpers.create_user(connection, user, user_password, is_proxy_relation)
        postgresql_helpers.create_database(connection, database, user)

        connection.close()

        if is_proxy_relation:
            # Connect to the created database to create the auth_query function,
            # enabling it to be used by the proxy application.
            connection = postgresql_helpers.connect_to_database(
                database, "postgres", self._primary_endpoint, self._get_postgres_password()
            )

            auth_function = postgresql_helpers.create_auth_query_function(connection)

            connection.close()

            self.postgresql_provides.set_proxy_auth(
                event.relation,
                user,
                user_password,
                auth_function,
            )

        self.postgresql_provides.set_endpoints(
            event.relation,
            postgresql_helpers.build_connection_string(
                database, user, self._primary_endpoint, user_password
            ),
        )
        self.postgresql_provides.set_read_only_endpoints(
            event.relation,
            [
                postgresql_helpers.build_connection_string(
                    database, user, self._replicas_endpoint, user_password
                )
            ],
        )

    @property
    def _primary_endpoint(self) -> str:
        """Returns the endpoint of the primary instance."""
        return self._build_service_name("primary")

    @property
    def _replicas_endpoint(self) -> str:
        """Returns the endpoint of the replicas instances."""
        return self._build_service_name("replicas")

    def _build_service_name(self, service: str) -> str:
        """Build a full k8s service name based on the service name."""
        return f"{self._name}-{service}.{self._namespace}.svc.cluster.local"

    def _on_upgrade_charm(self, _) -> None:
        # Add labels required for replication when the pod loses them (like when it's deleted).
        self._patch_pod_labels()

    def _patch_pod_labels(self) -> None:
        """Add labels required for replication to the current pod."""
        try:
            client = Client()
            patch = {
                "metadata": {"labels": {"application": "patroni", "cluster-name": self._namespace}}
            }
            client.patch(
                Pod,
                name=self._unit_name_to_pod_name(self._unit),
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

    def _on_update_status(self, _) -> None:
        # Until https://github.com/canonical/pebble/issues/6 is fixed,
        # we need to use the logic below to restart the leader
        # and a stuck replica after a failover/switchover.
        try:
            state = self._patroni.get_postgresql_state()
            if state == "restarting":
                # Restart the stuck replica.
                self._restart_postgresql_service()
            elif state == "stopping":
                # Change needed to force an immediate failover.
                self._patroni.change_master_start_timeout(0)
                # Restart the stuck previous leader (will trigger an immediate failover).
                self._restart_postgresql_service()
                # Revert configuration to default.
                self._patroni.change_master_start_timeout(None)
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
    def _pod_ip(self) -> str:
        """Current pod ip."""
        return self.model.get_binding(PEER).network.bind_address

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
            pod name in "psotgresql-k8s-0" format.
        """
        return unit_name.replace("/", "-")


if __name__ == "__main__":
    main(PostgresqlOperatorCharm, use_juju_for_storage=True)
