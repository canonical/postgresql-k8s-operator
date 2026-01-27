#!/usr/bin/env -S LD_LIBRARY_PATH=lib python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed Kubernetes Operator for the PostgreSQL database."""

import itertools
import json
import logging
import os
import re
import shutil
import sys
import time
from datetime import datetime
from functools import cached_property
from hashlib import shake_128
from pathlib import Path
from typing import Literal, get_args
from urllib.parse import urlparse

from authorisation_rules_observer import (
    AuthorisationRulesChangeCharmEvents,
    AuthorisationRulesObserver,
)

# First platform-specific import, will fail on wrong architecture
try:
    import psycopg2
except ModuleNotFoundError:
    from ops.main import main

    from arch_utils import WrongArchitectureWarningCharm, is_wrong_architecture

    # If the charm was deployed inside a host with different architecture
    # (possibly due to user specifying an incompatible revision)
    # then deploy an empty blocked charm with a warning.
    if is_wrong_architecture() and __name__ == "__main__":
        main(WrongArchitectureWarningCharm, use_juju_for_storage=True)
    raise

from charms.data_platform_libs.v0.data_interfaces import DataPeerData, DataPeerUnitData
from charms.data_platform_libs.v0.data_models import TypedCharmBase
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v1.loki_push_api import LogProxyConsumer
from charms.postgresql_k8s.v0.postgresql import (
    ACCESS_GROUP_IDENTITY,
    ACCESS_GROUPS,
    REQUIRED_PLUGINS,
    PostgreSQL,
    PostgreSQLEnableDisableExtensionError,
    PostgreSQLGetCurrentTimelineError,
    PostgreSQLListUsersError,
    PostgreSQLUpdateUserPasswordError,
)
from charms.postgresql_k8s.v0.postgresql_tls import PostgreSQLTLS
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.rolling_ops.v0.rollingops import RollingOpsManager, RunWithLock
from charms.tempo_coordinator_k8s.v0.charm_tracing import trace_charm
from charms.tempo_coordinator_k8s.v0.tracing import TracingEndpointRequirer
from lightkube import ApiError, Client
from lightkube.models.core_v1 import ServicePort, ServiceSpec
from lightkube.models.meta_v1 import ObjectMeta
from lightkube.resources.core_v1 import Endpoints, Node, Pod, Service
from ops import JujuVersion, main
from ops.charm import (
    ActionEvent,
    HookEvent,
    LeaderElectedEvent,
    RelationDepartedEvent,
    SecretRemoveEvent,
    WorkloadEvent,
)
from ops.model import (
    ActiveStatus,
    BlockedStatus,
    Container,
    MaintenanceStatus,
    ModelError,
    Relation,
    Unit,
    UnknownStatus,
    WaitingStatus,
)
from ops.pebble import (
    ChangeError,
    ExecError,
    Layer,
    PathError,
    ProtocolError,
    ServiceInfo,
    ServiceStatus,
)
from requests import ConnectionError as RequestsConnectionError
from tenacity import RetryError, Retrying, stop_after_attempt, stop_after_delay, wait_fixed

from backups import CANNOT_RESTORE_PITR, S3_BLOCK_MESSAGES, PostgreSQLBackups
from config import CharmConfig
from constants import (
    APP_SCOPE,
    BACKUP_USER,
    DATABASE_DEFAULT_NAME,
    DATABASE_PORT,
    METRICS_PORT,
    MONITORING_PASSWORD_KEY,
    MONITORING_USER,
    PATRONI_PASSWORD_KEY,
    PEER,
    PGBACKREST_METRICS_PORT,
    PLUGIN_OVERRIDES,
    POSTGRES_LOG_FILES,
    REPLICATION_PASSWORD_KEY,
    REPLICATION_USER,
    REWIND_PASSWORD_KEY,
    REWIND_USER,
    SECRET_DELETED_LABEL,
    SECRET_INTERNAL_LABEL,
    SECRET_KEY_OVERRIDES,
    SPI_MODULE,
    SYSTEM_USERS,
    TLS_CA_FILE,
    TLS_CERT_FILE,
    TLS_KEY_FILE,
    TRACING_PROTOCOL,
    TRACING_RELATION_NAME,
    UNIT_SCOPE,
    USER,
    USER_PASSWORD_KEY,
    WORKLOAD_OS_GROUP,
    WORKLOAD_OS_USER,
)
from ldap import PostgreSQLLDAP
from patroni import NotReadyError, Patroni, SwitchoverFailedError, SwitchoverNotSyncError
from relations.async_replication import (
    REPLICATION_CONSUMER_RELATION,
    REPLICATION_OFFER_RELATION,
    PostgreSQLAsyncReplication,
)
from relations.db import EXTENSIONS_BLOCKING_MESSAGE, DbProvides
from relations.postgresql_provider import PostgreSQLProvider
from upgrade import PostgreSQLUpgrade, get_postgresql_k8s_dependencies_model
from utils import any_cpu_to_cores, any_memory_to_bytes, new_password

logger = logging.getLogger(__name__)

EXTENSIONS_DEPENDENCY_MESSAGE = "Unsatisfied plugin dependencies. Please check the logs"
EXTENSION_OBJECT_MESSAGE = "Cannot disable plugins: Existing objects depend on it. See logs"
INSUFFICIENT_SIZE_WARNING = "<10% free space on pgdata volume."

ORIGINAL_PATRONI_ON_FAILURE_CONDITION = "restart"

# http{x,core} clutter the logs with debug messages
logging.getLogger("httpcore").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.ERROR)

Scopes = Literal[APP_SCOPE, UNIT_SCOPE]
PASSWORD_USERS = [*SYSTEM_USERS, "patroni"]


class CannotConnectError(Exception):
    """Cannot run smoke check on connected Database."""


@trace_charm(
    tracing_endpoint="tracing_endpoint",
    extra_types=(
        DbProvides,
        GrafanaDashboardProvider,
        LogProxyConsumer,
        MetricsEndpointProvider,
        Patroni,
        PostgreSQL,
        PostgreSQLAsyncReplication,
        PostgreSQLBackups,
        PostgreSQLLDAP,
        PostgreSQLProvider,
        PostgreSQLTLS,
        PostgreSQLUpgrade,
        RollingOpsManager,
    ),
)
class PostgresqlOperatorCharm(TypedCharmBase[CharmConfig]):
    """Charmed Operator for the PostgreSQL database."""

    config_type = CharmConfig
    on = AuthorisationRulesChangeCharmEvents()

    def __init__(self, *args):
        super().__init__(*args)

        # Support for disabling the operator.
        disable_file = Path(f"{os.environ.get('CHARM_DIR')}/disable")
        if disable_file.exists():
            logger.warning(
                f"\n\tDisable file `{disable_file.resolve()}` found, the charm will skip all events."
                "\n\tTo resume normal operations, please remove the file."
            )
            self.unit.status = BlockedStatus("Disabled")
            sys.exit(0)

        self.peer_relation_app = DataPeerData(
            self.model,
            relation_name=PEER,
            secret_field_name=SECRET_INTERNAL_LABEL,
            deleted_label=SECRET_DELETED_LABEL,
        )
        self.peer_relation_unit = DataPeerUnitData(
            self.model,
            relation_name=PEER,
            secret_field_name=SECRET_INTERNAL_LABEL,
            deleted_label=SECRET_DELETED_LABEL,
        )

        self.postgresql_service = "postgresql"
        self.rotate_logs_service = "rotate-logs"
        self.pgbackrest_server_service = "pgbackrest server"
        self.ldap_sync_service = "ldap-sync"
        self.metrics_service = "metrics_server"
        self.pgbackrest_metrics_service = "pgbackrest_metrics_service"
        self._unit = self.model.unit.name
        self._name = self.model.app.name
        self._namespace = self.model.name
        self._context = {"namespace": self._namespace, "app_name": self._name}
        self.cluster_name = f"patroni-{self._name}"

        run_cmd = (
            "/usr/bin/juju-exec" if self.model.juju_version.major > 2 else "/usr/bin/juju-run"
        )
        self._observer = AuthorisationRulesObserver(self, run_cmd)
        self.framework.observe(self.on.databases_change, self._on_databases_change)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.leader_elected, self._on_leader_elected)
        self.framework.observe(self.on[PEER].relation_changed, self._on_peer_relation_changed)
        self.framework.observe(self.on.secret_changed, self._on_peer_relation_changed)
        self.framework.observe(self.on[PEER].relation_departed, self._on_peer_relation_departed)
        self.framework.observe(self.on.postgresql_pebble_ready, self._on_postgresql_pebble_ready)
        self.framework.observe(self.on.pgdata_storage_detaching, self._on_pgdata_storage_detaching)
        self.framework.observe(self.on.stop, self._on_stop)
        self.framework.observe(self.on.get_password_action, self._on_get_password)
        self.framework.observe(self.on.set_password_action, self._on_set_password)
        self.framework.observe(self.on.promote_to_primary_action, self._on_promote_to_primary)
        self.framework.observe(self.on.get_primary_action, self._on_get_primary)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self.framework.observe(self.on.secret_remove, self._on_secret_remove)

        self._certs_path = "/usr/local/share/ca-certificates"
        self._storage_path = self.meta.storages["pgdata"].location
        self.pgdata_path = f"{self._storage_path}/pgdata"

        self.upgrade = PostgreSQLUpgrade(
            self,
            model=get_postgresql_k8s_dependencies_model(),
            relation_name="upgrade",
            substrate="k8s",
        )
        self.framework.observe(self.on.upgrade_charm, self._on_upgrade_charm)
        self.postgresql_client_relation = PostgreSQLProvider(self)
        self.legacy_db_relation = DbProvides(self, admin=False)
        self.legacy_db_admin_relation = DbProvides(self, admin=True)
        self.backup = PostgreSQLBackups(self, "s3-parameters")
        self.ldap = PostgreSQLLDAP(self, "ldap")
        self.tls = PostgreSQLTLS(self, PEER, [self.primary_endpoint, self.replicas_endpoint])
        self.async_replication = PostgreSQLAsyncReplication(self)
        self.restart_manager = RollingOpsManager(
            charm=self, relation="restart", callback=self._restart
        )
        self._observer.start_authorisation_rules_observer()
        self.grafana_dashboards = GrafanaDashboardProvider(self)
        self.metrics_endpoint = MetricsEndpointProvider(
            self,
            refresh_event=[self.on.start],
            jobs=self._generate_metrics_jobs(self.is_tls_enabled),
        )
        self.loki_push = LogProxyConsumer(
            self,
            logs_scheme={"postgresql": {"log-files": POSTGRES_LOG_FILES}},
            relation_name="logging",
        )

        if self.model.juju_version.supports_open_port_on_k8s:
            try:
                self.unit.set_ports(5432, 8008)
            except ModelError:
                logger.exception("failed to open port")
        self.tracing = TracingEndpointRequirer(
            self, relation_name=TRACING_RELATION_NAME, protocols=[TRACING_PROTOCOL]
        )

    def _on_databases_change(self, _):
        """Handle databases change event."""
        self.update_config()
        logger.debug("databases changed")
        timestamp = datetime.now()
        self._peers.data[self.unit].update({"pg_hba_needs_update_timestamp": str(timestamp)})
        logger.debug(f"authorisation rules changed at {timestamp}")

    @property
    def tracing_endpoint(self) -> str | None:
        """Otlp http endpoint for charm instrumentation."""
        if self.tracing.is_ready():
            return self.tracing.get_endpoint(TRACING_PROTOCOL)

    def _generate_metrics_jobs(self, enable_tls: bool) -> dict:
        """Generate spec for Prometheus scraping."""
        return [
            {"static_configs": [{"targets": [f"*:{METRICS_PORT}"]}]},
            {"static_configs": [{"targets": [f"*:{PGBACKREST_METRICS_PORT}"]}]},
            {
                "static_configs": [{"targets": ["*:8008"]}],
                "scheme": "https" if enable_tls else "http",
                "tls_config": {"insecure_skip_verify": True},
            },
        ]

    @property
    def app_units(self) -> set[Unit]:
        """The peer-related units in the application."""
        if not self._peers:
            return set()

        return {self.unit, *self._peers.units}

    def scoped_peer_data(self, scope: Scopes) -> dict | None:
        """Returns peer data based on scope."""
        if scope == APP_SCOPE:
            return self.app_peer_data
        elif scope == UNIT_SCOPE:
            return self.unit_peer_data

    @property
    def app_peer_data(self) -> dict:
        """Application peer relation data object."""
        relation = self.model.get_relation(PEER)
        if relation is None:
            return {}

        return relation.data[self.app]

    @property
    def unit_peer_data(self) -> dict:
        """Unit peer relation data object."""
        relation = self.model.get_relation(PEER)
        if relation is None:
            return {}

        return relation.data[self.unit]

    def _peer_data(self, scope: Scopes) -> dict:
        """Return corresponding databag for app/unit."""
        relation = self.model.get_relation(PEER)
        if relation is None:
            return {}

        return relation.data[self._scope_obj(scope)]

    def _scope_obj(self, scope: Scopes):
        if scope == APP_SCOPE:
            return self.app
        if scope == UNIT_SCOPE:
            return self.unit

    def peer_relation_data(self, scope: Scopes) -> DataPeerData:
        """Returns the peer relation data per scope."""
        if scope == APP_SCOPE:
            return self.peer_relation_app
        elif scope == UNIT_SCOPE:
            return self.peer_relation_unit

    def _translate_field_to_secret_key(self, key: str) -> str:
        """Change 'key' to secrets-compatible key field."""
        if not self.model.juju_version.has_secrets:
            return key
        key = SECRET_KEY_OVERRIDES.get(key, key)
        new_key = key.replace("_", "-")
        return new_key.strip("-")

    def get_secret(self, scope: Scopes, key: str) -> str | None:
        """Get secret from the secret storage."""
        if scope not in get_args(Scopes):
            raise RuntimeError("Unknown secret scope.")

        if not (peers := self.model.get_relation(PEER)):
            return None

        secret_key = self._translate_field_to_secret_key(key)
        # Old translation in databag is to be taken
        if result := self.peer_relation_data(scope).fetch_my_relation_field(peers.id, key):
            return result

        return self.peer_relation_data(scope).get_secret(peers.id, secret_key)

    def set_secret(self, scope: Scopes, key: str, value: str | None) -> str | None:
        """Set secret from the secret storage."""
        if scope not in get_args(Scopes):
            raise RuntimeError("Unknown secret scope.")

        if not value:
            return self.remove_secret(scope, key)

        if not (peers := self.model.get_relation(PEER)):
            return None

        secret_key = self._translate_field_to_secret_key(key)
        # Old translation in databag is to be deleted
        self.scoped_peer_data(scope).pop(key, None)
        self.peer_relation_data(scope).set_secret(peers.id, secret_key, value)

    def remove_secret(self, scope: Scopes, key: str) -> None:
        """Removing a secret."""
        if scope not in get_args(Scopes):
            raise RuntimeError("Unknown secret scope.")

        if not (peers := self.model.get_relation(PEER)):
            return None

        secret_key = self._translate_field_to_secret_key(key)

        self.peer_relation_data(scope).delete_relation_data(peers.id, [secret_key])

    @property
    def is_cluster_initialised(self) -> bool:
        """Returns whether the cluster is already initialised."""
        return "cluster_initialised" in self.app_peer_data

    @property
    def is_cluster_restoring_backup(self) -> bool:
        """Returns whether the cluster is restoring a backup."""
        return "restoring-backup" in self.app_peer_data

    @property
    def is_cluster_restoring_to_time(self) -> bool:
        """Returns whether the cluster is restoring a backup to a specific time."""
        return "restore-to-time" in self.app_peer_data

    @property
    def is_unit_departing(self) -> bool:
        """Returns whether the unit is departing."""
        return "departing" in self.unit_peer_data

    @property
    def is_unit_stopped(self) -> bool:
        """Returns whether the unit is stopped."""
        return "stopped" in self.unit_peer_data

    @cached_property
    def postgresql(self) -> PostgreSQL:
        """Returns an instance of the object used to interact with the database."""
        return PostgreSQL(
            primary_host=self.primary_endpoint,
            current_host=self.endpoint,
            user=USER,
            password=self.get_secret(APP_SCOPE, f"{USER}-password"),
            database=DATABASE_DEFAULT_NAME,
            system_users=SYSTEM_USERS,
        )

    @cached_property
    def endpoint(self) -> str:
        """Returns the endpoint of this instance's pod."""
        return f"{self._unit.replace('/', '-')}.{self._build_service_name('endpoints')}"

    @cached_property
    def primary_endpoint(self) -> str:
        """Returns the endpoint of the primary instance's service."""
        return self._build_service_name("primary")

    @cached_property
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

    def _get_endpoints_to_remove(self) -> list[str]:
        """List the endpoints that were part of the cluster but departed."""
        old = self._endpoints
        current = [self._get_hostname_from_unit(member) for member in self._hosts]
        endpoints_to_remove = list(set(old) - set(current))
        return endpoints_to_remove

    def get_unit_ip(self, unit: Unit) -> str | None:
        """Get the IP address of a specific unit."""
        # Check if host is current host.
        if unit == self.unit:
            return str(self.model.get_binding(PEER).network.bind_address)
        # Check if host is a peer.
        elif unit in self._peers.data:
            return str(self._peers.data[unit].get("private-address"))
        # Return None if the unit is not a peer neither the current unit.
        else:
            return None

    def updated_synchronous_node_count(self) -> bool:
        """Tries to update synchronous_node_count configuration and reports the result."""
        try:
            self._patroni.update_synchronous_node_count()
            return True
        except RetryError:
            logger.debug("Unable to set synchronous_node_count")
            return False

    def _on_peer_relation_departed(self, event: RelationDepartedEvent) -> None:
        """The leader removes the departing units from the list of cluster members."""
        # Allow leader to update endpoints if it isn't leaving.
        if not self.unit.is_leader() or event.departing_unit == self.unit:
            return

        if not self.is_cluster_initialised or not self.updated_synchronous_node_count():
            logger.debug(
                "Deferring on_peer_relation_departed: Cluster must be initialized before members can leave"
            )
            event.defer()
            return

        endpoints_to_remove = self._get_endpoints_to_remove()
        self.postgresql_client_relation.update_read_only_endpoint()
        self._remove_from_endpoints(endpoints_to_remove)

        # Update the sync-standby endpoint in the async replication data.
        self.async_replication.update_async_replication_data()

    def _on_pgdata_storage_detaching(self, _) -> None:
        # Change the primary if it's the unit that is being removed.
        try:
            primary = self._patroni.get_primary(unit_name_pattern=True)
        except RetryError:
            # Ignore the event if the primary couldn't be retrieved.
            # If a switchover is needed, an automatic failover will be triggered
            # when the unit is removed.
            logger.debug("Early exit on_pgdata_storage_detaching: primary cannot be retrieved")
            return

        if self.unit.name != primary:
            return

        if not self._patroni.are_all_members_ready():
            logger.warning(
                "could not switchover because not all members are ready"
                " - an automatic failover will be triggered"
            )
            return

        # Try to switchover to another member and raise an exception if it doesn't succeed.
        # If it doesn't happen on time, Patroni will automatically run a fail-over.
        try:
            # Get the current primary to check if it has changed later.
            current_primary = self._patroni.get_primary()

            # Trigger the switchover.
            self._patroni.switchover()

            # Wait for the switchover to complete.
            self._patroni.primary_changed(current_primary)

            logger.info("successful switchover")
        except (RetryError, SwitchoverFailedError) as e:
            logger.warning(
                f"switchover failed with reason: {e} - an automatic failover will be triggered"
            )
            return

        # Only update the connection endpoints if there is a primary.
        # A cluster can have all members as replicas for some time after
        # a failed switchover, so wait until the primary is elected.
        endpoints_to_remove = self._get_endpoints_to_remove()
        self.postgresql_client_relation.update_read_only_endpoint()
        self._remove_from_endpoints(endpoints_to_remove)

    def _on_peer_relation_changed(self, event: HookEvent) -> None:  # noqa: C901
        """Reconfigure cluster members."""
        # The cluster must be initialized first in the leader unit
        # before any other member joins the cluster.
        if not self.is_cluster_initialised:
            if self.unit.is_leader():
                if self._initialize_cluster(event):
                    logger.debug("Deferring on_peer_relation_changed: Leader initialized cluster")
                    event.defer()
                else:
                    logger.debug("_initialized_cluster failed on _peer_relation_changed")
                    return
            else:
                logger.debug(
                    "Early exit on_peer_relation_changed: Cluster must be initialized before members can join"
                )
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
        try:
            self.update_config()
        except ValueError as e:
            self.unit.status = BlockedStatus("Configuration Error. Please check the logs")
            logger.error("Invalid configuration: %s", str(e))
            return

        # Should not override a blocked status
        if isinstance(self.unit.status, BlockedStatus):
            logger.debug("on_peer_relation_changed early exit: Unit in blocked status")
            return

        services = container.pebble.get_services(names=[self.postgresql_service])
        if (
            (self.is_cluster_restoring_backup or self.is_cluster_restoring_to_time)
            and len(services) > 0
            and not self._was_restore_successful(container, services[0])
        ):
            logger.debug("on_peer_relation_changed early exit: Backup restore check failed")
            return

        # Validate the status of the member before setting an ActiveStatus.
        if not self._patroni.member_started:
            logger.debug("Deferring on_peer_relation_changed: Waiting for member to start")
            self.unit.status = WaitingStatus("awaiting for member to start")
            event.defer()
            return

        try:
            self.postgresql_client_relation.update_read_only_endpoint()
        except ModelError as e:
            logger.warning("Cannot update read_only endpoints: %s", str(e))

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
            self.unit_peer_data.pop("start-tls-server", None)

        self.backup.coordinate_stanza_fields()

        # This is intended to be executed only when leader is reinitializing S3 connection due to the leader change.
        if (
            "s3-initialization-start" in self.app_peer_data
            and "s3-initialization-done" not in self.unit_peer_data
            and self.is_primary
            and not self.backup._on_s3_credential_changed_primary(event)
        ):
            return

        # Clean-up unit initialization data after successful sync to the leader.
        if "s3-initialization-done" in self.app_peer_data and not self.unit.is_leader():
            self.unit_peer_data.update({
                "stanza": "",
                "s3-initialization-block-message": "",
                "s3-initialization-done": "",
                "s3-initialization-start": "",
            })

        self.async_replication.handle_read_only_mode()

    def _on_config_changed(self, event) -> None:
        """Handle configuration changes, like enabling plugins."""
        if not self.is_cluster_initialised:
            logger.debug("Defer on_config_changed: cluster not initialised yet")
            event.defer()
            return

        if not self.upgrade.idle:
            logger.debug("Defer on_config_changed: upgrade in progress")
            event.defer()
            return

        try:
            self._validate_config_options()
            # update config on every run
            self.update_config()
        except psycopg2.OperationalError:
            logger.debug("Defer on_config_changed: Cannot connect to database")
            event.defer()
            return
        except ValueError as e:
            self.unit.status = BlockedStatus("Configuration Error. Please check the logs")
            logger.error("Invalid configuration: %s", str(e))
            return
        if not self.updated_synchronous_node_count():
            logger.debug("Defer on_config_changed: unable to set synchronous node count")
            event.defer()
            return

        if self.is_blocked and "Configuration Error" in self.unit.status.message:
            self._set_active_status()

        # Update the sync-standby endpoint in the async replication data.
        self.async_replication.update_async_replication_data()

        if not self.unit.is_leader():
            return

        # Enable and/or disable the extensions.
        self.enable_disable_extensions()

        self._unblock_extensions()

    def _unblock_extensions(self) -> None:
        # Unblock the charm after extensions are enabled (only if it's blocked due to application
        # charms requesting extensions).
        if self.unit.status.message != EXTENSIONS_BLOCKING_MESSAGE:
            return

        for relation in [
            *self.model.relations.get("db", []),
            *self.model.relations.get("db-admin", []),
        ]:
            if not self.legacy_db_relation.set_up_relation(relation):
                logger.debug(
                    "Early exit on_config_changed: legacy relation requested extensions that are still disabled"
                )
                return

    def enable_disable_extensions(self, database: str | None = None) -> None:
        """Enable/disable PostgreSQL extensions set through config options.

        Args:
            database: optional database where to enable/disable the extension.
        """
        if self._patroni.get_primary() is None:
            logger.debug("Early exit enable_disable_extensions: standby cluster")
            return
        original_status = self.unit.status
        extensions = {}
        # collect extensions
        for plugin in self.config.plugin_keys():
            enable = self.config[plugin]

            # Enable or disable the plugin/extension.
            extension = "_".join(plugin.split("_")[1:-1])
            if extension == "spi":
                for ext in SPI_MODULE:
                    extensions[ext] = enable
                continue
            extension = PLUGIN_OVERRIDES.get(extension, extension)
            if self._check_extension_dependencies(extension, enable):
                self.unit.status = BlockedStatus(EXTENSIONS_DEPENDENCY_MESSAGE)
                return
            extensions[extension] = enable
        if self.is_blocked and self.unit.status.message == EXTENSIONS_DEPENDENCY_MESSAGE:
            self._set_active_status()
            original_status = self.unit.status

        self._handle_enable_disable_extensions(original_status, extensions, database)

    def _handle_enable_disable_extensions(self, original_status, extensions, database) -> None:
        """Try enablind/disabling Postgresql extensions and handle exceptions appropriately."""
        if not isinstance(original_status, UnknownStatus):
            self.unit.status = WaitingStatus("Updating extensions")
        try:
            self.postgresql.enable_disable_extensions(extensions, database)
        except psycopg2.errors.DependentObjectsStillExist as e:
            logger.error(
                "Failed to disable plugin: %s\nWas the plugin enabled manually? If so, update charm config with `juju config postgresql-k8s plugin_<plugin_name>_enable=True`",
                str(e),
            )
            self.unit.status = BlockedStatus(EXTENSION_OBJECT_MESSAGE)
            return
        except PostgreSQLEnableDisableExtensionError as e:
            logger.exception("failed to change plugins: %s", str(e))
        if original_status.message == EXTENSION_OBJECT_MESSAGE:
            self._set_active_status()
            return
        if not isinstance(original_status, UnknownStatus):
            self.unit.status = original_status

    def _check_extension_dependencies(self, extension: str, enable: bool) -> bool:
        skip = False
        if enable and extension in REQUIRED_PLUGINS:
            for ext in REQUIRED_PLUGINS[extension]:
                if not self.config[f"plugin_{ext}_enable"]:
                    skip = True
                    logger.exception(
                        "cannot enable %s, extension required %s to be enabled before",
                        extension,
                        ext,
                    )
        return skip

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
        if not self.is_cluster_initialised:
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
            self._patroni.update_synchronous_node_count()
        except NotReadyError:
            logger.info("Deferring reconfigure: another member doing sync right now")
            event.defer()
        except RetryError:
            logger.info("Deferring reconfigure: failed to obtain cluster members from Patroni")
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
        for password in {
            USER_PASSWORD_KEY,
            REPLICATION_PASSWORD_KEY,
            REWIND_PASSWORD_KEY,
            MONITORING_PASSWORD_KEY,
            PATRONI_PASSWORD_KEY,
        }:
            if self.get_secret(APP_SCOPE, password) is None:
                self.set_secret(APP_SCOPE, password, new_password())

        # Add this unit to the list of cluster members
        # (the cluster should start with only this member).
        if self._endpoint not in self._endpoints:
            self._add_to_endpoints(self._endpoint)

        self._cleanup_old_cluster_resources()

        if not self.fix_leader_annotation():
            return

        # Create resources and add labels needed for replication.
        if self.upgrade.idle:
            try:
                self._create_services()
            except ApiError:
                logger.exception("failed to create k8s services")
                self.unit.status = BlockedStatus("failed to create k8s services")
                return

        # Remove departing units when the leader changes.
        self._remove_from_endpoints(self._get_endpoints_to_remove())

        self._add_members(event)

    def fix_leader_annotation(self) -> bool:
        """Fix the leader annotation if it's missing."""
        client = Client()
        try:
            endpoint = client.get(Endpoints, name=self.cluster_name, namespace=self._namespace)
            if "leader" not in endpoint.metadata.annotations:
                patch = {
                    "metadata": {
                        "annotations": {"leader": self._unit_name_to_pod_name(self._unit)}
                    }
                }
                client.patch(
                    Endpoints, name=self.cluster_name, namespace=self._namespace, obj=patch
                )
                self.app_peer_data.pop("cluster_initialised", None)
                logger.info("Fixed missing leader annotation")
        except ApiError as e:
            if e.status.code == 403:
                self.on_deployed_without_trust()
                return False
            # Ignore the error only when the resource doesn't exist.
            if e.status.code != 404:
                raise e
        return True

    def _create_pgdata(self, container: Container):
        """Create the PostgreSQL data directory."""
        if not container.exists(self.pgdata_path):
            container.make_dir(
                self.pgdata_path, permissions=0o750, user=WORKLOAD_OS_USER, group=WORKLOAD_OS_GROUP
            )
        # Also, fix the permissions from the parent directory.
        container.exec([
            "chown",
            f"{WORKLOAD_OS_USER}:{WORKLOAD_OS_GROUP}",
            self._storage_path,
        ]).wait()

    def _on_postgresql_pebble_ready(self, event: WorkloadEvent) -> None:
        """Event handler for PostgreSQL container on PebbleReadyEvent."""
        if self._endpoint in self._endpoints:
            self._fix_pod()

        # TODO: move this code to an "_update_layer" method in order to also utilize it in
        # config-changed hook.
        # Get the postgresql container so we can configure/manipulate it.
        container = event.workload
        if not container.can_connect():
            logger.debug(
                "Defer on_postgresql_pebble_ready: Waiting for container to become available"
            )
            event.defer()
            return

        # Create the PostgreSQL data directory. This is needed on cloud environments
        # where the volume is mounted with more restrictive permissions.
        self._create_pgdata(container)

        self.unit.set_workload_version(self._patroni.rock_postgresql_version)

        # Defer the initialization of the workload in the replicas
        # if the cluster hasn't been bootstrap on the primary yet.
        # Otherwise, each unit will create a different cluster and
        # any update in the members list on the units won't have effect
        # on fixing that.
        if not self.unit.is_leader() and not self.is_cluster_initialised:
            logger.debug(
                "Deferring on_postgresql_pebble_ready: Not leader and cluster not initialized"
            )
            event.defer()
            return

        try:
            self.push_tls_files_to_workload()
            for ca_secret_name in self.tls.get_ca_secret_names():
                self.push_ca_file_into_workload(ca_secret_name)
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

        # Enable/disable PostgreSQL extensions if they were set before the cluster
        # was fully initialised.
        self.enable_disable_extensions()

        # Enable pgbackrest service
        self.backup.start_stop_pgbackrest_service()

        # All is well, set an ActiveStatus.
        self._set_active_status()

    def _set_active_status(self):
        # The charm should not override this status outside of the function checking disk space.
        if self.unit.status.message == INSUFFICIENT_SIZE_WARNING:
            return
        try:
            if self.unit.is_leader() and "s3-initialization-block-message" in self.app_peer_data:
                self.unit.status = BlockedStatus(
                    self.app_peer_data["s3-initialization-block-message"]
                )
                return
            if (
                self._patroni.get_primary(unit_name_pattern=True) == self.unit.name
                or self.is_standby_leader
            ):
                danger_state = ""
                if len(self._patroni.get_running_cluster_members()) < self.app.planned_units():
                    danger_state = " (degraded)"
                self.unit.status = ActiveStatus(
                    f"{'Standby' if self.is_standby_leader else 'Primary'}{danger_state}"
                )
            elif self._patroni.member_started:
                self.unit.status = ActiveStatus()
        except (RetryError, RequestsConnectionError) as e:
            logger.error(f"failed to get primary with error {e}")

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
        if self.upgrade.idle:
            try:
                self._create_services()
            except ApiError:
                logger.exception("failed to create k8s services")
                self.unit.status = BlockedStatus("failed to create k8s services")
                return False

        async_replication_primary_cluster = self.async_replication.get_primary_cluster()
        if (
            async_replication_primary_cluster is not None
            and async_replication_primary_cluster != self.app
        ):
            logger.debug(
                "Early exit _initialize_cluster: not the primary cluster in async replication"
            )
            return True

        if not self._patroni.primary_endpoint_ready:
            logger.debug(
                "Deferring on_postgresql_pebble_ready: Waiting for primary endpoint to be ready"
            )
            self.unit.status = WaitingStatus("awaiting for primary endpoint to be ready")
            event.defer()
            return False

        pg_users = self.postgresql.list_users()
        # Create the backup user.
        if BACKUP_USER not in pg_users:
            self.postgresql.create_user(BACKUP_USER, new_password(), admin=True)
        # Create the monitoring user.
        if MONITORING_USER not in pg_users:
            self.postgresql.create_user(
                MONITORING_USER,
                self.get_secret(APP_SCOPE, MONITORING_PASSWORD_KEY),
                extra_user_roles=["pg_monitor"],
            )

        self.postgresql.set_up_database()

        access_groups = self.postgresql.list_access_groups()
        if access_groups != set(ACCESS_GROUPS):
            self.postgresql.create_access_groups()
            self.postgresql.grant_internal_access_group_memberships()

        # Mark the cluster as initialised.
        self._peers.data[self.app]["cluster_initialised"] = "True"

        return True

    @property
    def is_blocked(self) -> bool:
        """Returns whether the unit is in a blocked state."""
        return isinstance(self.unit.status, BlockedStatus)

    def _on_upgrade_charm(self, _) -> None:
        self._fix_pod()

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
            "primary": "primary",
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

    def _cleanup_old_cluster_resources(self) -> None:
        """Delete kubernetes services and endpoints from previous deployment."""
        if self.is_cluster_initialised:
            logger.debug("Early exit _cleanup_old_cluster_resources: cluster already initialised")
            return

        client = Client()
        for kind, suffix in itertools.product([Service, Endpoints], ["", "-config", "-sync"]):
            try:
                client.delete(
                    res=kind,
                    name=f"{self.cluster_name}{suffix}",
                    namespace=self._namespace,
                )
                logger.info(f"deleted {kind.__name__}/{self.cluster_name}{suffix}")
            except ApiError as e:
                if e.status.code == 403:
                    self.on_deployed_without_trust()
                    return
                # Ignore the error only when the resource doesn't exist.
                if e.status.code != 404:
                    raise e

    @property
    def _has_blocked_status(self) -> bool:
        """Returns whether the unit is in a blocked state."""
        return isinstance(self.unit.status, BlockedStatus)

    @property
    def _has_non_restore_waiting_status(self) -> bool:
        """Returns whether the unit is in a waiting state and there is no restore process ongoing."""
        return (
            isinstance(self.unit.status, WaitingStatus)
            and not self.is_cluster_restoring_backup
            and not self.is_cluster_restoring_to_time
        )

    def _on_get_password(self, event: ActionEvent) -> None:
        """Returns the password for a user as an action response.

        If no user is provided, the password of the operator user is returned.
        """
        username = event.params.get("username", USER)
        if username not in PASSWORD_USERS and self.is_ldap_enabled:
            event.fail("The action can be run only for system users when LDAP is enabled")
            return
        if username not in PASSWORD_USERS:
            event.fail(
                f"The action can be run only for system users or Patroni:"
                f" {', '.join(PASSWORD_USERS)} not {username}"
            )
            return

        event.set_results({"password": self.get_secret(APP_SCOPE, f"{username}-password")})

    def _on_set_password(self, event: ActionEvent) -> None:  # noqa: C901
        """Set the password for the specified user."""
        # Only leader can write the new password into peer relation.
        if not self.unit.is_leader():
            event.fail("The action can be run only on leader unit")
            return

        if not (username := event.params.get("username")):
            event.fail("The action requires a username")
            return
        if username not in SYSTEM_USERS and self.is_ldap_enabled:
            event.fail("The action can be run only for system users when LDAP is enabled")
            return
        if username not in SYSTEM_USERS:
            event.fail(
                f"The action can be run only for system users:"
                f" {', '.join(SYSTEM_USERS)} not {username}"
            )
            return

        password = new_password()
        if "password" in event.params:
            password = event.params["password"]

        if password == self.get_secret(APP_SCOPE, f"{username}-password"):
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

        replication_offer_relation = self.model.get_relation(REPLICATION_OFFER_RELATION)
        if (
            replication_offer_relation is not None
            and not self.async_replication.is_primary_cluster()
        ):
            # Update the password in the other cluster PostgreSQL primary instance.
            other_cluster_endpoints = self.async_replication.get_all_primary_cluster_endpoints()
            other_cluster_primary = self._patroni.get_primary(
                alternative_endpoints=other_cluster_endpoints
            )
            other_cluster_primary_ip = next(
                replication_offer_relation.data[unit].get("private-address")
                for unit in replication_offer_relation.units
                if unit.name.replace("/", "-") == other_cluster_primary
            )
            try:
                self.postgresql.update_user_password(
                    username, password, database_host=other_cluster_primary_ip
                )
            except PostgreSQLUpdateUserPasswordError as e:
                logger.exception(e)
                event.fail("Failed changing the password.")
                return
        elif self.model.get_relation(REPLICATION_CONSUMER_RELATION) is not None:
            event.fail(
                "Failed changing the password: This action can be ran only in the cluster from the offer side."
            )
            return
        else:
            # Update the password in this cluster PostgreSQL primary instance.
            try:
                self.postgresql.update_user_password(username, password)
            except PostgreSQLUpdateUserPasswordError as e:
                logger.exception(e)
                event.fail("Failed changing the password.")
                return

        # Update the password in the secret store.
        self.set_secret(APP_SCOPE, f"{username}-password", password)

        # Update and reload Patroni configuration in this unit to use the new password.
        # Other units Patroni configuration will be reloaded in the peer relation changed event.
        self.update_config()

        event.set_results({"password": password})

    def _on_promote_to_primary(self, event: ActionEvent) -> None:
        if event.params.get("scope") == "cluster":
            return self.async_replication.promote_to_primary(event)
        elif event.params.get("scope") == "unit":
            return self.promote_primary_unit(event)
        else:
            event.fail("Scope should be either cluster or unit")

    def promote_primary_unit(self, event: ActionEvent) -> None:
        """Handles promote to primary for unit scope."""
        if event.params.get("force"):
            event.fail("Suprerfluous force flag with unit scope")
        else:
            try:
                self._patroni.switchover(self.unit.name, wait=False)
            except SwitchoverNotSyncError:
                event.fail("Unit is not sync standby")
            except SwitchoverFailedError:
                event.fail("Switchover failed or timed out, check the logs for details")

    def _on_secret_remove(self, event: SecretRemoveEvent) -> None:
        if self.model.juju_version < JujuVersion("3.6.11"):
            logger.warning(
                "Skipping secret revision removal due to https://github.com/juju/juju/issues/20782"
            )
            return

        # A secret removal (entire removal, not just a revision removal) causes
        # https://github.com/juju/juju/issues/20794. This check is to avoid the
        # errors that would happen if we tried to remove the revision in that case
        # (in the revision removal, the label is present).
        if event.secret.label is None:
            logger.debug("Secret with no label cannot be removed")
            return
        logger.debug(f"Removing secret with label {event.secret.label} revision {event.revision}")
        event.remove_revision()

    def _on_get_primary(self, event: ActionEvent) -> None:
        """Get primary instance."""
        try:
            primary = self._patroni.get_primary(unit_name_pattern=True)
            event.set_results({"primary": primary})
        except RetryError as e:
            logger.error(f"failed to get primary with error {e}")

    def _fix_pod(self) -> None:
        # Recreate k8s resources and add labels required for replication
        # when the pod loses them (like when it's deleted).
        if self.upgrade.idle:
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

        # Update the sync-standby endpoint in the async replication data.
        self.async_replication.update_async_replication_data()

    def _on_stop(self, _):
        # Remove data from the drive when scaling down to zero to prevent
        # the cluster from getting stuck when scaling back up.
        if self.app.planned_units() == 0:
            self.unit_peer_data.clear()

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
                # Since Juju 3.6.13 (commit aa38cff0b1), the mutating webhook no longer
                # processes Endpoints - they were removed from the webhook's resource
                # allowlist. Query Patroni-created resources separately.
                resources_to_patch.extend(
                    client.list(
                        kind,
                        namespace=self._namespace,
                        labels={
                            "application": "patroni",
                            "cluster-name": f"patroni-{self._name}",
                        },
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
                type(resource) is Service
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

    def _on_update_status_early_exit_checks(self, container) -> bool:
        if not self.upgrade.idle:
            logger.debug("Early exit on_update_status: upgrade in progress")
            return False

        if not container.can_connect():
            logger.debug("on_update_status early exit: Cannot connect to container")
            return False

        self._check_pgdata_storage_size()

        if (
            self._has_blocked_status and self.unit.status not in S3_BLOCK_MESSAGES
        ) or self._has_non_restore_waiting_status:
            # If charm was failing to disable plugin, try again and continue (user may have removed the objects)
            if self.unit.status.message == EXTENSION_OBJECT_MESSAGE:
                self.enable_disable_extensions()
                return True

            logger.debug("on_update_status early exit: Unit is in Blocked/Waiting status")
            return False
        return True

    def _check_pgdata_storage_size(self) -> None:
        """Asserts that pgdata volume has at least 10% free space and blocks charm if not."""
        try:
            total_size, _, free_size = shutil.disk_usage(self.pgdata_path)
        except FileNotFoundError:
            logger.error("pgdata folder not found in %s", self.pgdata_path)
            return

        logger.debug(
            "pgdata free disk space: %s out of %s, ratio of %s",
            free_size,
            total_size,
            free_size / total_size,
        )
        if free_size / total_size < 0.1:
            self.unit.status = BlockedStatus(INSUFFICIENT_SIZE_WARNING)
        elif self.unit.status.message == INSUFFICIENT_SIZE_WARNING:
            self.unit.status = ActiveStatus()
            self._set_active_status()

    def _on_update_status(self, _) -> None:
        """Update the unit status message."""
        container = self.unit.get_container("postgresql")
        if not self._on_update_status_early_exit_checks(container):
            return

        services = container.pebble.get_services(names=[self.postgresql_service])
        if len(services) == 0:
            # Service has not been added nor started yet, so don't try to check Patroni API.
            logger.debug("on_update_status early exit: Service has not been added nor started yet")
            return

        if (
            not self.is_cluster_restoring_backup
            and not self.is_cluster_restoring_to_time
            and not self.is_unit_stopped
            and services[0].current != ServiceStatus.ACTIVE
        ):
            logger.warning(
                f"{self.postgresql_service} pebble service inactive, restarting service"
            )
            try:
                container.restart(self.postgresql_service)
            except ChangeError:
                logger.exception("Failed to restart patroni")
            # If service doesn't recover fast, exit and wait for next hook run to re-check
            if not self._patroni.member_started:
                self.unit.status = MaintenanceStatus("Database service inactive, restarting")
                return

        if (
            self.is_cluster_restoring_backup or self.is_cluster_restoring_to_time
        ) and not self._was_restore_successful(container, services[0]):
            return

        # Update the sync-standby endpoint in the async replication data.
        self.async_replication.update_async_replication_data()

        self.backup.coordinate_stanza_fields()

        self._set_active_status()

    def _was_restore_successful(self, container: Container, service: ServiceInfo) -> bool:
        """Checks if restore operation succeeded and S3 is properly configured."""
        if self.is_cluster_restoring_to_time and all(self.is_pitr_failed(container)):
            logger.error(
                "Restore failed: database service failed to reach point-in-time-recovery target. "
                "You can launch another restore with different parameters"
            )
            self.log_pitr_last_transaction_time()
            self.unit.status = BlockedStatus(CANNOT_RESTORE_PITR)
            return False

        if (
            service.current != ServiceStatus.ACTIVE
            and self.unit.status.message != CANNOT_RESTORE_PITR
        ):
            logger.error("Restore failed: database service failed to start")
            self.unit.status = BlockedStatus("Failed to restore backup")
            return False

        if not self._patroni.member_started:
            logger.debug("Restore check early exit: Patroni has not started yet")
            return False

        restoring_backup = self.app_peer_data.get("restoring-backup")
        restore_timeline = self.app_peer_data.get("restore-timeline")
        restore_to_time = self.app_peer_data.get("restore-to-time")
        try:
            current_timeline = self.postgresql.get_current_timeline()
        except PostgreSQLGetCurrentTimelineError:
            logger.debug("Restore check early exit: can't get current wal timeline")
            return False

        # Remove the restoring backup flag and the restore stanza name.
        self.app_peer_data.update({
            "restoring-backup": "",
            "restore-stanza": "",
            "restore-to-time": "",
            "restore-timeline": "",
        })
        self.update_config()
        self.restore_patroni_on_failure_condition()

        logger.info(
            "Restored"
            f"{f' to {restore_to_time}' if restore_to_time else ''}"
            f"{f' from timeline {restore_timeline}' if restore_timeline and not restoring_backup else ''}"
            f"{f' from backup {self.backup._parse_backup_id(restoring_backup)[0]}' if restoring_backup else ''}"
            f". Currently tracking the newly created timeline {current_timeline}."
        )

        can_use_s3_repository, validation_message = self.backup.can_use_s3_repository()
        if not can_use_s3_repository:
            self.app_peer_data.update({
                "stanza": "",
                "s3-initialization-start": "",
                "s3-initialization-done": "",
                "s3-initialization-block-message": validation_message,
            })

        return True

    @cached_property
    def _patroni(self):
        """Returns an instance of the Patroni object."""
        return Patroni(
            self,
            self._endpoint,
            self._endpoints,
            self.primary_endpoint,
            self._namespace,
            self._storage_path,
            self.get_secret(APP_SCOPE, USER_PASSWORD_KEY),
            self.get_secret(APP_SCOPE, REPLICATION_PASSWORD_KEY),
            self.get_secret(APP_SCOPE, REWIND_PASSWORD_KEY),
            self.get_secret(APP_SCOPE, PATRONI_PASSWORD_KEY),
        )

    @property
    def is_connectivity_enabled(self) -> bool:
        """Return whether this unit can be connected externally."""
        return self.unit_peer_data.get("connectivity", "on") == "on"

    @property
    def is_ldap_charm_related(self) -> bool:
        """Return whether this unit has an LDAP charm related."""
        return self.app_peer_data.get("ldap_enabled", "False") == "True"

    @property
    def is_ldap_enabled(self) -> bool:
        """Return whether this unit has LDAP enabled."""
        return self.is_ldap_charm_related and self.is_cluster_initialised

    @property
    def is_primary(self) -> bool:
        """Return whether this unit is the primary instance."""
        return self._unit == self._patroni.get_primary(unit_name_pattern=True)

    @property
    def is_standby_leader(self) -> bool:
        """Return whether this unit is the standby leader instance."""
        return self._unit == self._patroni.get_standby_leader(unit_name_pattern=True)

    @property
    def is_tls_enabled(self) -> bool:
        """Return whether TLS is enabled."""
        if not self.model.get_relation(PEER):
            return False
        return all(self.tls.get_tls_files())

    @property
    def is_peer_data_tls_set(self) -> bool:
        """Return whether the TLS flag is raised in the peer data."""
        return bool(self.unit_peer_data.get("tls"))

    @property
    def _endpoint(self) -> str:
        """Current unit hostname."""
        return self._get_hostname_from_unit(self._unit_name_to_pod_name(self.unit.name))

    @property
    def _endpoints(self) -> list[str]:
        """Cluster members hostnames."""
        if self._peers:
            return json.loads(self._peers.data[self.app].get("endpoints", "[]"))
        else:
            # If the peer relations was not created yet, return only the current member hostname.
            return [self._endpoint]

    @property
    def peer_members_endpoints(self) -> list[str]:
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

    def _remove_from_endpoints(self, endpoints: list[str]) -> None:
        """Remove endpoints from the members list."""
        self._update_endpoints(endpoints_to_remove=endpoints)

    def _update_endpoints(
        self,
        endpoint_to_add: str | None = None,
        endpoints_to_remove: list[str] | None = None,
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

    def _generate_ldap_service(self) -> dict:
        """Generate the LDAP service definition."""
        ldap_params = self.get_ldap_parameters()

        ldap_url = urlparse(ldap_params["ldapurl"])
        ldap_host = ldap_url.hostname
        ldap_port = ldap_url.port

        ldap_base_dn = ldap_params["ldapbasedn"]
        ldap_bind_username = ldap_params["ldapbinddn"]
        ldap_bind_password = ldap_params["ldapbindpasswd"]
        ldap_group_mappings = self.postgresql.build_postgresql_group_map(self.config.ldap_map)

        return {
            "override": "replace",
            "summary": "synchronize LDAP users",
            "command": "/start-ldap-synchronizer.sh",
            "startup": "enabled",
            "environment": {
                "LDAP_HOST": ldap_host,
                "LDAP_PORT": ldap_port,
                "LDAP_BASE_DN": ldap_base_dn,
                "LDAP_BIND_USERNAME": ldap_bind_username,
                "LDAP_BIND_PASSWORD": ldap_bind_password,
                "LDAP_GROUP_IDENTITY": json.dumps(ACCESS_GROUP_IDENTITY),
                "LDAP_GROUP_MAPPINGS": json.dumps(ldap_group_mappings),
                "POSTGRES_HOST": "127.0.0.1",
                "POSTGRES_PORT": DATABASE_PORT,
                "POSTGRES_DATABASE": DATABASE_DEFAULT_NAME,
                "POSTGRES_USERNAME": USER,
                "POSTGRES_PASSWORD": self.get_secret(APP_SCOPE, USER_PASSWORD_KEY),
            },
        }

    def _generate_metrics_service(self) -> dict:
        """Generate the postgresql metrics service definition."""
        return {
            "override": "replace",
            "summary": "postgresql metrics exporter",
            "command": "/start-exporter.sh",
            "startup": (
                "enabled"
                if self.get_secret("app", MONITORING_PASSWORD_KEY) is not None
                else "disabled"
            ),
            "after": [self.postgresql_service],
            "user": WORKLOAD_OS_USER,
            "group": WORKLOAD_OS_GROUP,
            "environment": {
                "DATA_SOURCE_NAME": (
                    f"user={MONITORING_USER} "
                    f"password={self.get_secret('app', MONITORING_PASSWORD_KEY)} "
                    "host=/var/run/postgresql port=5432 database=postgres"
                ),
            },
        }

    def _generate_pgbackrest_metrics_service(self) -> dict:
        """Generate the pgbackrest metrics service definition."""
        return {
            "override": "replace",
            "summary": "pgbackrest metrics exporter",
            "command": "/usr/bin/pgbackrest_exporter",
            "startup": "enabled",
            "after": [self.postgresql_service],
            "user": WORKLOAD_OS_USER,
            "group": WORKLOAD_OS_GROUP,
        }

    def _postgresql_layer(self) -> Layer:
        """Returns a Pebble configuration layer for PostgreSQL."""
        pod_name = self._unit_name_to_pod_name(self._unit)
        layer_config = {
            "summary": "postgresql + patroni layer",
            "description": "pebble config layer for postgresql + patroni",
            "services": {
                self.postgresql_service: {
                    "override": "replace",
                    "summary": "entrypoint of the postgresql + patroni image",
                    "command": f"patroni {self._storage_path}/patroni.yml",
                    "startup": "enabled",
                    "on-failure": self.unit_peer_data.get(
                        "patroni-on-failure-condition-override", None
                    )
                    or ORIGINAL_PATRONI_ON_FAILURE_CONDITION,
                    "user": WORKLOAD_OS_USER,
                    "group": WORKLOAD_OS_GROUP,
                    "environment": {
                        "PATRONI_KUBERNETES_LABELS": f"{{application: patroni, cluster-name: {self.cluster_name}}}",
                        "PATRONI_KUBERNETES_LEADER_LABEL_VALUE": "primary",
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
                self.ldap_sync_service: {
                    "override": "replace",
                    "summary": "synchronize LDAP users",
                    "command": "/start-ldap-synchronizer.sh",
                    "startup": "disabled",
                },
                self.metrics_service: self._generate_metrics_service(),
                self.pgbackrest_metrics_service: self._generate_pgbackrest_metrics_service(),
                self.rotate_logs_service: {
                    "override": "replace",
                    "summary": "rotate logs",
                    "command": "python3 /home/postgres/rotate_logs.py",
                    "startup": "disabled",
                },
            },
            "checks": {
                self.postgresql_service: {
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

    def _push_file_to_workload(self, container: Container, file_path: str, file_data: str) -> None:
        """Uploads a file into the provided container."""
        container.push(
            file_path,
            file_data,
            make_dirs=True,
            permissions=0o400,
            user=WORKLOAD_OS_USER,
            group=WORKLOAD_OS_GROUP,
        )

    def push_tls_files_to_workload(self) -> bool:
        """Uploads TLS files to the workload container."""
        container = self.unit.get_container("postgresql")

        key, ca, cert = self.tls.get_tls_files()

        if key is not None:
            self._push_file_to_workload(container, f"{self._storage_path}/{TLS_KEY_FILE}", key)
        if ca is not None:
            self._push_file_to_workload(container, f"{self._storage_path}/{TLS_CA_FILE}", ca)
            self._push_file_to_workload(container, f"{self._certs_path}/ca.crt", ca)
            container.exec(["update-ca-certificates"]).wait()
        if cert is not None:
            self._push_file_to_workload(container, f"{self._storage_path}/{TLS_CERT_FILE}", cert)

        return self.update_config()

    def push_ca_file_into_workload(self, secret_name: str) -> bool:
        """Uploads CA certificate into the workload container."""
        container = self.unit.get_container("postgresql")
        certificates = self.get_secret(UNIT_SCOPE, secret_name)

        if certificates is not None:
            self._push_file_to_workload(
                container=container,
                file_path=f"{self._certs_path}/{secret_name}.crt",
                file_data=certificates,
            )
            container.exec(["update-ca-certificates"]).wait()

        return self.update_config()

    def clean_ca_file_from_workload(self, secret_name: str) -> bool:
        """Cleans up CA certificate from the workload container."""
        container = self.unit.get_container("postgresql")
        container.remove_path(f"{self._certs_path}/{secret_name}.crt")
        container.exec(["update-ca-certificates"]).wait()

        return self.update_config()

    def _restart(self, event: RunWithLock) -> None:
        """Restart PostgreSQL."""
        if not self._patroni.are_all_members_ready():
            logger.debug("Early exit _restart: not all members ready yet")
            event.defer()
            return

        try:
            logger.debug("Restarting PostgreSQL")
            self._patroni.restart_postgresql()
        except RetryError:
            error_message = "failed to restart PostgreSQL"
            logger.exception(error_message)
            self.unit.status = BlockedStatus(error_message)
            return

        # Update health check URL.
        self._update_pebble_layers()

        try:
            for attempt in Retrying(wait=wait_fixed(3), stop=stop_after_delay(300)):
                with attempt:
                    if not self._can_connect_to_postgresql:
                        raise CannotConnectError
        except Exception:
            logger.exception("Unable to reconnect to postgresql")

        # Start or stop the pgBackRest TLS server service when TLS certificate change.
        self.backup.start_stop_pgbackrest_service()

    def _restart_metrics_service(self) -> None:
        """Restart the monitoring service if the password was rotated."""
        container = self.unit.get_container("postgresql")
        current_layer = container.get_plan()

        metrics_service = current_layer.services[self.metrics_service]
        data_source_name = metrics_service.environment.get("DATA_SOURCE_NAME", "")

        if metrics_service and not data_source_name.startswith(
            f"user={MONITORING_USER} password={self.get_secret('app', MONITORING_PASSWORD_KEY)} "
        ):
            container.add_layer(
                self.metrics_service,
                Layer({"services": {self.metrics_service: self._generate_metrics_service()}}),
                combine=True,
            )
            container.restart(self.metrics_service)

    def _restart_ldap_sync_service(self) -> None:
        """Restart the LDAP sync service in case any configuration changed."""
        if not self._patroni.member_started:
            logger.debug("Restart LDAP sync early exit: Patroni has not started yet")
            return

        container = self.unit.get_container("postgresql")
        sync_service = container.pebble.get_services(names=[self.ldap_sync_service])

        if not self.is_primary and sync_service[0].is_running():
            logger.debug("Stopping LDAP sync service. It must only run in the primary")
            container.stop(self.ldap_sync_service)

        if self.is_primary and not self.is_ldap_enabled:
            logger.debug("Stopping LDAP sync service")
            container.stop(self.ldap_sync_service)
            return

        if self.is_primary and self.is_ldap_enabled:
            container.add_layer(
                self.ldap_sync_service,
                Layer({"services": {self.ldap_sync_service: self._generate_ldap_service()}}),
                combine=True,
            )
            logger.debug("Starting LDAP sync service")
            container.restart(self.ldap_sync_service)

    @property
    def _is_workload_running(self) -> bool:
        """Returns whether the workload is running (in an active state)."""
        container = self.unit.get_container("postgresql")
        if not container.can_connect():
            return False

        services = container.pebble.get_services(names=[self.postgresql_service])
        if len(services) == 0:
            return False

        return services[0].current == ServiceStatus.ACTIVE

    @property
    def _can_connect_to_postgresql(self) -> bool:
        try:
            for attempt in Retrying(stop=stop_after_delay(10), wait=wait_fixed(3)):
                with attempt:
                    if not self.postgresql.get_postgresql_timezones():
                        logger.debug("Cannot connect to database (CannotConnectError)")
                        raise CannotConnectError
        except RetryError:
            logger.debug("Cannot connect to database (RetryError)")
            return False
        return True

    def _calculate_max_worker_processes(self, cpu_cores: int) -> str | None:
        """Calculate cpu_max_worker_processes configuration value."""
        if self.config.cpu_max_worker_processes == "auto":
            # auto = minimum(8, 2 * vCores)
            return str(min(8, 2 * cpu_cores))
        elif self.config.cpu_max_worker_processes is not None:
            value = self.config.cpu_max_worker_processes
            if value < 2:
                from pydantic import ValidationError
                from pydantic_core import InitErrorDetails

                raise ValidationError.from_exception_data(
                    "ValidationError",
                    [
                        InitErrorDetails(
                            type="greater_than_equal",
                            ctx={"ge": 2},
                            input=value,
                            loc=("cpu_max_worker_processes",),
                        )
                    ],
                )
            cap = 10 * cpu_cores
            if value > cap:
                raise ValueError(
                    f"cpu_max_worker_processes value {value} exceeds maximum allowed "
                    f"of {cap} (10 * vCores). Please set a value <= {cap}."
                )
            return str(value)
        return None

    def _validate_worker_config_value(self, param_name: str, value: int, cpu_cores: int) -> str:
        """Shared validation logic for worker process parameters.

        Args:
            param_name: The configuration parameter name (for error messages)
            value: The integer value to validate
            cpu_cores: The number of available CPU cores

        Returns:
            String representation of the validated value

        Raises:
            ValidationError: If value is less than 2
            ValueError: If value exceeds 10 * vCores
        """
        if value < 2:
            from pydantic import ValidationError
            from pydantic_core import InitErrorDetails

            raise ValidationError.from_exception_data(
                "ValidationError",
                [
                    InitErrorDetails(
                        type="greater_than_equal",
                        ctx={"ge": 2},
                        input=value,
                        loc=(param_name,),
                    )
                ],
            )
        cap = 10 * cpu_cores
        if value > cap:
            raise ValueError(
                f"{param_name} value {value} exceeds maximum allowed "
                f"of {cap} (10 * vCores). Please set a value <= {cap}."
            )
        return str(value)

    def _calculate_max_parallel_workers(self, base_max_workers: int, cpu_cores: int) -> str | None:
        """Calculate cpu_max_parallel_workers configuration value."""
        if self.config.cpu_max_parallel_workers == "auto":
            return str(base_max_workers)
        elif self.config.cpu_max_parallel_workers is not None:
            # Validate the value first
            validated_value_str = self._validate_worker_config_value(
                "cpu_max_parallel_workers", self.config.cpu_max_parallel_workers, cpu_cores
            )
            # Apply the min constraint with base_max_workers
            return str(min(int(validated_value_str), base_max_workers))
        return None

    def _calculate_max_parallel_maintenance_workers(
        self, base_max_workers: int, cpu_cores: int
    ) -> str | None:
        """Calculate cpu_max_parallel_maintenance_workers configuration value."""
        if self.config.cpu_max_parallel_maintenance_workers == "auto":
            return str(base_max_workers)
        elif self.config.cpu_max_parallel_maintenance_workers is not None:
            return self._validate_worker_config_value(
                "cpu_max_parallel_maintenance_workers",
                self.config.cpu_max_parallel_maintenance_workers,
                cpu_cores,
            )
        return None

    def _calculate_max_logical_replication_workers(
        self, base_max_workers: int, cpu_cores: int
    ) -> str | None:
        """Calculate cpu_max_logical_replication_workers configuration value."""
        if self.config.cpu_max_logical_replication_workers == "auto":
            return str(base_max_workers)
        elif self.config.cpu_max_logical_replication_workers is not None:
            return self._validate_worker_config_value(
                "cpu_max_logical_replication_workers",
                self.config.cpu_max_logical_replication_workers,
                cpu_cores,
            )
        return None

    def _calculate_max_sync_workers_per_subscription(
        self, base_max_workers: int, cpu_cores: int
    ) -> str | None:
        """Calculate cpu_max_sync_workers_per_subscription configuration value."""
        if self.config.cpu_max_sync_workers_per_subscription == "auto":
            return str(base_max_workers)
        elif self.config.cpu_max_sync_workers_per_subscription is not None:
            return self._validate_worker_config_value(
                "cpu_max_sync_workers_per_subscription",
                self.config.cpu_max_sync_workers_per_subscription,
                cpu_cores,
            )
        return None

    def _calculate_worker_process_config(self, cpu_cores: int) -> dict[str, str]:
        """Calculate worker process configuration values.

        Handles 'auto' values and capping logic for worker process parameters.
        Returns a dictionary with the calculated values ready for PostgreSQL.
        """
        result: dict[str, str] = {}

        # Calculate cpu_max_worker_processes (baseline for other worker configs)
        cpu_max_worker_processes_value = self._calculate_max_worker_processes(cpu_cores)
        if cpu_max_worker_processes_value is not None:
            result["max_worker_processes"] = cpu_max_worker_processes_value

        # Get the effective cpu_max_worker_processes for dependent configs
        # Use the calculated value, or fall back to PostgreSQL default (8)
        base_max_workers = int(result.get("max_worker_processes", "8"))

        # Calculate other worker parameters
        cpu_max_parallel_workers_value = self._calculate_max_parallel_workers(
            base_max_workers, cpu_cores
        )
        if cpu_max_parallel_workers_value is not None:
            result["max_parallel_workers"] = cpu_max_parallel_workers_value

        cpu_max_parallel_maintenance_workers_value = (
            self._calculate_max_parallel_maintenance_workers(base_max_workers, cpu_cores)
        )
        if cpu_max_parallel_maintenance_workers_value is not None:
            result["max_parallel_maintenance_workers"] = cpu_max_parallel_maintenance_workers_value

        cpu_max_logical_replication_workers_value = (
            self._calculate_max_logical_replication_workers(base_max_workers, cpu_cores)
        )
        if cpu_max_logical_replication_workers_value is not None:
            result["max_logical_replication_workers"] = cpu_max_logical_replication_workers_value

        cpu_max_sync_workers_per_subscription_value = (
            self._calculate_max_sync_workers_per_subscription(base_max_workers, cpu_cores)
        )
        if cpu_max_sync_workers_per_subscription_value is not None:
            result["max_sync_workers_per_subscription"] = (
                cpu_max_sync_workers_per_subscription_value
            )

        return result

    def _build_postgresql_parameters(
        self, available_cpu_cores: int, available_memory: int
    ) -> dict | None:
        """Build PostgreSQL configuration parameters.

        Args:
            available_cpu_cores: Number of available CPU cores
            available_memory: Available memory in bytes

        Returns:
            Dictionary of PostgreSQL parameters or None if base parameters couldn't be built.
        """
        limit_memory = None
        if self.config.profile_limit_memory:
            limit_memory = self.config.profile_limit_memory * 10**6

        # Build PostgreSQL parameters.
        pg_parameters = self.postgresql.build_postgresql_parameters(
            self.model.config, available_memory, limit_memory
        )

        # Calculate and merge worker process configurations
        worker_configs = self._calculate_worker_process_config(available_cpu_cores)

        # Add cpu_wal_compression configuration (separate from worker processes)
        if self.config.cpu_wal_compression is not None:
            cpu_wal_compression = "on" if self.config.cpu_wal_compression else "off"
        else:
            # Use config.yaml default when unset (default: true)
            cpu_wal_compression = "on"

        if pg_parameters is not None:
            pg_parameters.update(worker_configs)
            pg_parameters["wal_compression"] = cpu_wal_compression
        else:
            pg_parameters = dict(worker_configs)
            pg_parameters["wal_compression"] = cpu_wal_compression
            logger.debug(f"pg_parameters set to worker_configs = {pg_parameters}")

        return pg_parameters

    def update_config(self, is_creating_backup: bool = False) -> bool:
        """Updates Patroni config file based on the existence of the TLS files."""
        # Retrieve PostgreSQL parameters.
        try:
            available_cpu_cores, available_memory = self.get_available_resources()
        except ApiError as e:
            if e.status.code == 403:
                self.on_deployed_without_trust()
                return
            raise e

        postgresql_parameters = self._build_postgresql_parameters(
            available_cpu_cores, available_memory
        )

        # Extract worker configs for later use in Patroni API
        worker_configs = self._calculate_worker_process_config(available_cpu_cores)

        logger.info("Updating Patroni config file")
        # Update and reload configuration based on TLS files availability.
        self._patroni.render_patroni_yml_file(
            connectivity=self.is_connectivity_enabled,
            is_creating_backup=is_creating_backup,
            enable_ldap=self.is_ldap_enabled,
            enable_tls=self.is_tls_enabled,
            is_no_sync_member=self.upgrade.is_no_sync_member,
            backup_id=self.app_peer_data.get("restoring-backup"),
            pitr_target=self.app_peer_data.get("restore-to-time"),
            restore_timeline=self.app_peer_data.get("restore-timeline"),
            restore_to_latest=self.app_peer_data.get("restore-to-time", None) == "latest",
            stanza=self.app_peer_data.get("stanza", self.unit_peer_data.get("stanza")),
            restore_stanza=self.app_peer_data.get("restore-stanza"),
            parameters=postgresql_parameters,
            user_databases_map=self.relations_user_databases_map,
        )

        if not self._is_workload_running:
            # If Patroni/PostgreSQL has not started yet and TLS relations was initialised,
            # then mark TLS as enabled. This commonly happens when the charm is deployed
            # in a bundle together with the TLS certificates operator. This flag is used to
            # know when to call the Patroni API using HTTP or HTTPS.
            self.unit_peer_data.update({"tls": "enabled" if self.is_tls_enabled else ""})
            self.postgresql_client_relation.update_tls_flag(
                "True" if self.is_tls_enabled else "False"
            )
            logger.debug("Early exit update_config: Workload not started yet")
            return True

        if not self._patroni.member_started:
            logger.debug("Early exit update_config: Patroni not started yet")
            return False

        # Use config value if set, calculate otherwise
        if self.config.experimental_max_connections:
            max_connections = self.config.experimental_max_connections
        else:
            max_connections = max(4 * available_cpu_cores, 100)

        # Build the config patch with restart-required parameters
        config_patch = {
            "max_connections": max_connections,
            "max_prepared_transactions": self.config.memory_max_prepared_transactions,
            "shared_buffers": self.config.memory_shared_buffers,
            "wal_keep_size": self.config.durability_wal_keep_size,
        }

        # Add restart-required worker process parameters via Patroni API
        if "max_worker_processes" in worker_configs:
            config_patch["max_worker_processes"] = worker_configs["max_worker_processes"]
        if "max_logical_replication_workers" in worker_configs:
            config_patch["max_logical_replication_workers"] = worker_configs[
                "max_logical_replication_workers"
            ]

        self._patroni.bulk_update_parameters_controller_by_patroni(config_patch)

        self._handle_postgresql_restart_need(
            self.unit_peer_data.get("config_hash") != self.generate_config_hash
        )
        self._restart_metrics_service()
        self._restart_ldap_sync_service()

        self.unit_peer_data.update({
            "user_hash": self.generate_user_hash,
            "config_hash": self.generate_config_hash,
        })
        if self.unit.is_leader():
            self.app_peer_data.update({"user_hash": self.generate_user_hash})
        return True

    def _validate_config_options(self) -> None:
        """Validates specific config options that need access to the database or to the TLS status."""
        if (
            self.config.instance_default_text_search_config
            not in self.postgresql.get_postgresql_text_search_configs()
        ):
            raise ValueError(
                "instance_default_text_search_config config option has an invalid value"
            )

        if not self.postgresql.validate_group_map(self.config.ldap_map):
            raise ValueError("ldap_map config option has an invalid value")

        if not self.postgresql.validate_date_style(self.config.request_date_style):
            raise ValueError("request_date_style config option has an invalid value")

        if self.config.request_time_zone not in self.postgresql.get_postgresql_timezones():
            raise ValueError("request_time_zone config option has an invalid value")

        if (
            self.config.storage_default_table_access_method
            not in self.postgresql.get_postgresql_default_table_access_methods()
        ):
            raise ValueError(
                "storage_default_table_access_method config option has an invalid value"
            )

        container = self.unit.get_container("postgresql")
        output, _ = container.exec(["locale", "-a"]).wait_output()
        locales = list(output.splitlines())
        for parameter in ["response_lc_monetary", "response_lc_numeric", "response_lc_time"]:
            value = self.model.config.get(parameter)
            if value is not None and value not in locales:
                raise ValueError(
                    f"Value for {parameter} not one of the locales available in the system"
                )

    def _handle_postgresql_restart_need(self, config_changed: bool):
        """Handle PostgreSQL restart need based on the TLS configuration and configuration changes."""
        restart_postgresql = self.is_tls_enabled != self.postgresql.is_tls_enabled()
        try:
            self._patroni.reload_patroni_configuration()
        except Exception as e:
            logger.error(f"Reload patroni call failed! error: {e!s}")
        if config_changed and not restart_postgresql:
            # Wait for some more time than the Patroni's loop_wait default value (10 seconds),
            # which tells how much time Patroni will wait before checking the configuration
            # file again to reload it.
            try:
                for attempt in Retrying(stop=stop_after_attempt(5), wait=wait_fixed(3)):
                    with attempt:
                        restart_postgresql = (
                            restart_postgresql or self.postgresql.is_restart_pending()
                        )
                        if not restart_postgresql:
                            raise Exception
            except RetryError:
                # Ignore the error, as it happens only to indicate that the configuration has not changed.
                pass
        self.unit_peer_data.update({"tls": "enabled" if self.is_tls_enabled else ""})
        self.postgresql_client_relation.update_tls_flag("True" if self.is_tls_enabled else "False")

        # Restart PostgreSQL if TLS configuration has changed
        # (so the both old and new connections use the configuration).
        if restart_postgresql:
            logger.info("PostgreSQL restart required")
            self.metrics_endpoint.update_scrape_job_spec(
                self._generate_metrics_jobs(self.is_tls_enabled)
            )
            self.on[self.restart_manager.name].acquire_lock.emit()

    def _update_pebble_layers(self, replan: bool = True) -> None:
        """Update the pebble layers to keep the health check URL up-to-date."""
        container = self.unit.get_container("postgresql")

        # Get the current layer.
        current_layer = container.get_plan()

        # Create a new config layer.
        new_layer = self._postgresql_layer()

        # Check if there are any changes to layer services.
        if current_layer.services != new_layer.services:
            # Changes were made, add the new layer.
            container.add_layer(self.postgresql_service, new_layer, combine=True)
            logging.info("Added updated layer 'postgresql' to Pebble plan")
            if replan:
                container.replan()
                logging.info("Restarted postgresql service")
        if current_layer.checks != new_layer.checks:
            # Changes were made, add the new layer.
            container.add_layer(self.postgresql_service, new_layer, combine=True)
            logging.info("Updated health checks")

    def _unit_name_to_pod_name(self, unit_name: str) -> str:
        """Converts unit name to pod name.

        Args:
            unit_name: name in "postgresql-k8s/0" format.

        Returns:
            pod name in "postgresql-k8s-0" format.
        """
        return unit_name.replace("/", "-")

    def _get_node_name_for_pod(self) -> str:
        """Return the node name for a given pod."""
        client = Client()
        pod = client.get(
            Pod, name=self._unit_name_to_pod_name(self.unit.name), namespace=self._namespace
        )
        return pod.spec.nodeName

    def get_resources_limits(self, container_name: str) -> dict:
        """Return resources limits for a given container.

        Args:
            container_name: name of the container to get resources limits for
        """
        client = Client()
        pod = client.get(
            Pod, self._unit_name_to_pod_name(self.unit.name), namespace=self._namespace
        )

        for container in pod.spec.containers:
            if container.name == container_name:
                return container.resources.limits or {}
        return {}

    def get_node_allocable_memory(self) -> int:
        """Return the allocable memory in bytes for the current K8S node."""
        client = Client()
        node = client.get(Node, name=self._get_node_name_for_pod(), namespace=self._namespace)
        return any_memory_to_bytes(node.status.allocatable["memory"])

    def get_node_cpu_cores(self) -> int:
        """Return the number of CPU cores for the current K8S node."""
        client = Client()
        node = client.get(Node, name=self._get_node_name_for_pod(), namespace=self._namespace)
        return any_cpu_to_cores(node.status.allocatable["cpu"])

    def get_available_resources(self) -> tuple[int, int]:
        """Get available CPU cores and memory (in bytes) for the container."""
        cpu_cores = self.get_node_cpu_cores()
        allocable_memory = self.get_node_allocable_memory()
        container_limits = self.get_resources_limits(container_name="postgresql")
        if "cpu" in container_limits:
            cpu_str = container_limits["cpu"]
            constrained_cpu = int(cpu_str)
            if constrained_cpu < cpu_cores:
                logger.debug(f"CPU constrained to {cpu_str} cores from resource limit")
                cpu_cores = constrained_cpu
        if "memory" in container_limits:
            memory_str = container_limits["memory"]
            constrained_memory = any_memory_to_bytes(memory_str)
            if constrained_memory < allocable_memory:
                logger.debug(f"Memory constrained to {memory_str} from resource limit")
                allocable_memory = constrained_memory

        return cpu_cores, allocable_memory

    def on_deployed_without_trust(self) -> None:
        """Blocks the application and returns a specific error message for deployments made without --trust."""
        self.unit.status = BlockedStatus(
            f"Insufficient permissions, try: `juju trust {self._name} --scope=cluster`"
        )
        logger.error(
            f"""
            Access to k8s cluster resources is not authorized. This happens when RBAC is enabled and the deployed application was not trusted by the juju admin.
            To fix this issue, run `juju trust {self._name} --scope=cluster` (or remove & re-deploy {self._name} with `--trust`)
            """
        )

    @property
    def client_relations(self) -> list[Relation]:
        """Return the list of established client relations."""
        relations = []
        for relation_name in ["database", "db", "db-admin"]:
            for relation in self.model.relations.get(relation_name, []):
                relations.append(relation)
        return relations

    @property
    def relations_user_databases_map(self) -> dict:
        """Returns a user->databases map for all relations."""
        # Copy relations users directly instead of waiting for them to be created
        user_database_map = self._collect_user_relations()

        if not self.is_cluster_initialised or not self._patroni.member_started:
            user_database_map.update({
                USER: "all",
                REPLICATION_USER: "all",
                REWIND_USER: "all",
            })
            return user_database_map
        try:
            for user in self.postgresql.list_users(current_host=self.is_connectivity_enabled):
                if user in (
                    "backup",
                    "monitoring",
                    "operator",
                    "postgres",
                    "replication",
                    "rewind",
                ):
                    continue
                user_database_map[user] = ",".join(
                    sorted(
                        self.postgresql.list_accessible_databases_for_user(
                            user, current_host=self.is_connectivity_enabled
                        )
                    )
                )
            if self.postgresql.list_access_groups(
                current_host=self.is_connectivity_enabled
            ) != set(ACCESS_GROUPS):
                user_database_map.update({
                    USER: "all",
                    REPLICATION_USER: "all",
                    REWIND_USER: "all",
                })
            return user_database_map
        except PostgreSQLListUsersError:
            logger.debug("relations_user_databases_map: Unable to get users")
            return {USER: "all", REPLICATION_USER: "all", REWIND_USER: "all"}

    def _collect_user_relations(self) -> dict[str, str]:
        user_db_pairs = {}
        for relation in self.client_relations:
            user = f"relation_id_{relation.id}"
            if relation.name == "database":
                if (
                    database
                    := self.postgresql_client_relation.database_provides.fetch_relation_field(
                        relation.id, "database"
                    )
                ):
                    user_db_pairs[user] = database
            else:
                if database := relation.data.get(self.unit, {}).get("database"):
                    user_db_pairs[user] = database
        return user_db_pairs

    @cached_property
    def generate_user_hash(self) -> str:
        """Generate expected user and database hash."""
        return shake_128(str(self._collect_user_relations()).encode()).hexdigest(16)

    @cached_property
    def generate_config_hash(self) -> str:
        """Generate current configuration hash."""
        return shake_128(str(self.config.dict()).encode()).hexdigest(16)

    def override_patroni_on_failure_condition(
        self, new_condition: str, repeat_cause: str | None
    ) -> bool:
        """Temporary override Patroni pebble service on-failure condition.

        Executes only on current unit.

        Args:
            new_condition: new Patroni pebble service on-failure condition.
            repeat_cause: whether this field is equal to the last success override operation repeat cause, Patroni
                on-failure condition will be overridden (keeping the original restart condition reference untouched) and
                success code will be returned. But if this field is distinct from previous repeat cause or None,
                repeated operation will cause failure code will be returned.
        """
        if "patroni-on-failure-condition-override" in self.unit_peer_data:
            current_condition = self.unit_peer_data["patroni-on-failure-condition-override"]
            if repeat_cause is None:
                logger.error(
                    f"failure trying to override patroni on-failure condition to {new_condition}"
                    f"as it already overridden from {ORIGINAL_PATRONI_ON_FAILURE_CONDITION} to {current_condition}"
                )
                return False
            previous_repeat_cause = self.unit_peer_data.get(
                "overridden-patroni-on-failure-condition-repeat-cause", None
            )
            if previous_repeat_cause != repeat_cause:
                logger.error(
                    f"failure trying to override patroni on-failure condition to {new_condition}"
                    f"as it already overridden from {ORIGINAL_PATRONI_ON_FAILURE_CONDITION} to {current_condition}"
                    f"and repeat cause is not equal: {previous_repeat_cause} != {repeat_cause}"
                )
                return False
            self.unit_peer_data["patroni-on-failure-condition-override"] = new_condition
            self._update_pebble_layers(False)
            logger.debug(
                f"Patroni on-failure condition re-overridden to {new_condition} within repeat cause {repeat_cause}"
                f"(original on-failure condition reference is untouched and is {ORIGINAL_PATRONI_ON_FAILURE_CONDITION})"
            )
            return True

        self.unit_peer_data["patroni-on-failure-condition-override"] = new_condition
        if repeat_cause:
            self.unit_peer_data["overridden-patroni-on-failure-condition-repeat-cause"] = (
                repeat_cause
            )
        self._update_pebble_layers(False)
        logger.debug(
            f"Patroni on-failure condition overridden from {ORIGINAL_PATRONI_ON_FAILURE_CONDITION} to {new_condition}"
            f"{' with repeat cause ' + repeat_cause if repeat_cause is not None else ''}"
        )
        return True

    def restore_patroni_on_failure_condition(self) -> None:
        """Restore Patroni pebble service original on-failure condition.

        Will do nothing if not overridden. Executes only on current unit.
        """
        if "patroni-on-failure-condition-override" in self.unit_peer_data:
            self.unit_peer_data.update({
                "patroni-on-failure-condition-override": "",
                "overridden-patroni-on-failure-condition-repeat-cause": "",
            })
            self._update_pebble_layers(False)
            logger.debug(
                f"restored Patroni on-failure condition to {ORIGINAL_PATRONI_ON_FAILURE_CONDITION}"
            )
        else:
            logger.warning("not restoring patroni on-failure condition as it's not overridden")

    def is_pitr_failed(self, container: Container) -> tuple[bool, bool]:
        """Check if Patroni service failed to bootstrap cluster during point-in-time-recovery.

        Typically, this means that database service failed to reach point-in-time-recovery target or has been
        supplied with bad PITR parameter. Also, remembers last state and can provide info is it new event, or
        it belongs to previous action. Executes only on current unit.

        Returns:
            tuple[bool, bool]:
                - Is patroni service failed to bootstrap cluster.
                - Is it new fail, that wasn't observed previously.
        """
        patroni_exceptions = []
        count = 0
        while len(patroni_exceptions) == 0 and count < 10:
            if count > 0:
                time.sleep(3)
            try:
                log_exec = container.pebble.exec(
                    ["pebble", "logs", "postgresql", "-n", "all"], combine_stderr=True
                )
                patroni_logs = log_exec.wait_output()[0]
                patroni_exceptions = re.findall(
                    r"^([0-9-:TZ.]+) \[postgresql] patroni\.exceptions\.PatroniFatalException: Failed to bootstrap cluster$",
                    patroni_logs,
                    re.MULTILINE,
                )
            except ExecError:  # For Juju 2.
                log_exec = container.pebble.exec(["cat", "/var/log/postgresql/patroni.log"])
                patroni_logs = log_exec.wait_output()[0]
                patroni_exceptions = re.findall(
                    r"^([0-9- :]+) UTC \[[0-9]+\]: INFO: removing initialize key after failed attempt to bootstrap the cluster",
                    patroni_logs,
                    re.MULTILINE,
                )
                if len(patroni_exceptions) != 0:
                    break
                # If no match, look at older logs
                log_exec = container.pebble.exec([
                    "find",
                    "/var/log/postgresql/",
                    "-name",
                    "'patroni.log.*'",
                    "-exec",
                    "cat",
                    "{}",
                    "+",
                ])
                patroni_logs = log_exec.wait_output()[0]
                patroni_exceptions = re.findall(
                    r"^([0-9- :]+) UTC \[[0-9]+\]: INFO: removing initialize key after failed attempt to bootstrap the cluster",
                    patroni_logs,
                    re.MULTILINE,
                )
            count += 1

        if len(patroni_exceptions) > 0:
            logger.debug("Failures to bootstrap cluster detected on Patroni service logs")
            old_pitr_fail_id = self.unit_peer_data.get("last_pitr_fail_id", None)
            self.unit_peer_data["last_pitr_fail_id"] = patroni_exceptions[-1]
            return True, patroni_exceptions[-1] != old_pitr_fail_id

        logger.debug("No failures detected on Patroni service logs")
        return False, False

    def log_pitr_last_transaction_time(self) -> None:
        """Log to user last completed transaction time acquired from postgresql logs."""
        postgresql_logs = self._patroni.last_postgresql_logs()
        log_time = re.findall(
            r"last completed transaction was at log time (.*)$",
            postgresql_logs,
            re.MULTILINE,
        )
        if len(log_time) > 0:
            logger.info(f"Last completed transaction was at {log_time[-1]}")
        else:
            logger.error("Can't tell last completed transaction time")

    def get_plugins(self) -> list[str]:
        """Return a list of installed plugins."""
        plugins = [
            "_".join(plugin.split("_")[1:-1])
            for plugin in self.config.plugin_keys()
            if self.config[plugin]
        ]
        plugins = [PLUGIN_OVERRIDES.get(plugin, plugin) for plugin in plugins]
        if "spi" in plugins:
            plugins.remove("spi")
            for ext in SPI_MODULE:
                plugins.append(ext)
        return plugins

    def get_ldap_parameters(self) -> dict:
        """Returns the LDAP configuration to use."""
        if not self.is_cluster_initialised:
            return {}
        if not self.is_ldap_charm_related:
            logger.debug("LDAP is not enabled")
            return {}

        relation_data = self.ldap.get_relation_data()
        if relation_data is None:
            return {}

        params = {
            "ldapbasedn": relation_data.base_dn,
            "ldapbinddn": relation_data.bind_dn,
            "ldapbindpasswd": relation_data.bind_password,
            "ldaptls": relation_data.starttls,
            "ldapurl": relation_data.urls[0],
        }

        # LDAP authentication parameters that are exclusive to
        # one of the two supported modes (simple bind or search+bind)
        # must be put at the very end of the parameters string
        params.update({
            "ldapsearchfilter": self.config.ldap_search_filter,
        })

        return params


if __name__ == "__main__":
    main(PostgresqlOperatorCharm, use_juju_for_storage=True)
