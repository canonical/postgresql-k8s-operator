#!/usr/bin/env -S LD_LIBRARY_PATH=lib python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed Kubernetes Operator for the PostgreSQL database."""

import itertools
import json
import logging
import os
import pathlib
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
from refresh import PostgreSQLRefresh

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

import charm_refresh
from charms.data_platform_libs.v0.data_interfaces import DataPeerData, DataPeerUnitData
from charms.data_platform_libs.v1.data_models import TypedCharmBase
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v1.loki_push_api import LogProxyConsumer
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.rolling_ops.v0.rollingops import RollingOpsManager, RunWithLock
from charms.tempo_coordinator_k8s.v0.charm_tracing import trace_charm
from charms.tempo_coordinator_k8s.v0.tracing import TracingEndpointRequirer
from lightkube import ApiError, Client
from lightkube.models.core_v1 import ServicePort, ServiceSpec
from lightkube.models.meta_v1 import ObjectMeta
from lightkube.resources.core_v1 import Endpoints, Node, Pod, Service
from ops import (
    ActionEvent,
    ActiveStatus,
    BlockedStatus,
    CharmEvents,
    Container,
    HookEvent,
    LeaderElectedEvent,
    MaintenanceStatus,
    ModelError,
    Relation,
    RelationDepartedEvent,
    SecretChangedEvent,
    SecretNotFoundError,
    SecretRemoveEvent,
    StatusBase,
    Unit,
    UnknownStatus,
    WaitingStatus,
    WorkloadEvent,
    main,
)
from ops.log import JujuLogHandler
from ops.pebble import (
    ChangeError,
    ExecError,
    Layer,
    LayerDict,
    PathError,
    ProtocolError,
    ServiceDict,
    ServiceInfo,
    ServiceStatus,
)
from requests import ConnectionError as RequestsConnectionError
from single_kernel_postgresql.config.literals import (
    Substrates,
)
from single_kernel_postgresql.utils.postgresql import (
    ACCESS_GROUP_IDENTITY,
    ACCESS_GROUPS,
    REQUIRED_PLUGINS,
    PostgreSQL,
    PostgreSQLCreatePredefinedRolesError,
    PostgreSQLCreateUserError,
    PostgreSQLEnableDisableExtensionError,
    PostgreSQLGetCurrentTimelineError,
    PostgreSQLGrantDatabasePrivilegesToUserError,
    PostgreSQLListGroupsError,
    PostgreSQLListUsersError,
    PostgreSQLUpdateUserPasswordError,
)
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
    TLS_CA_BUNDLE_FILE,
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
from relations.logical_replication import (
    LOGICAL_REPLICATION_VALIDATION_ERROR_STATUS,
    PostgreSQLLogicalReplication,
)
from relations.postgresql_provider import PostgreSQLProvider
from relations.tls import TLS
from relations.tls_transfer import TLSTransfer
from utils import any_cpu_to_cores, any_memory_to_bytes, new_password

logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)

EXTENSIONS_DEPENDENCY_MESSAGE = "Unsatisfied plugin dependencies. Please check the logs"
EXTENSION_OBJECT_MESSAGE = "Cannot disable plugins: Existing objects depend on it. See logs"
INSUFFICIENT_SIZE_WARNING = "<10% free space on pgdata volume."

ORIGINAL_PATRONI_ON_FAILURE_CONDITION = "restart"

# http{x,core} clutter the logs with debug messages
logging.getLogger("httpcore").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.ERROR)

Scopes = Literal["app", "unit"]
PASSWORD_USERS = [*SYSTEM_USERS, "patroni"]


class CannotConnectError(Exception):
    """Cannot run smoke check on connected Database."""


@trace_charm(
    tracing_endpoint="tracing_endpoint",
    extra_types=(
        GrafanaDashboardProvider,
        LogProxyConsumer,
        MetricsEndpointProvider,
        Patroni,
        PostgreSQL,
        PostgreSQLAsyncReplication,
        PostgreSQLBackups,
        PostgreSQLLDAP,
        PostgreSQLProvider,
        TLS,
        RollingOpsManager,
    ),
)
class PostgresqlOperatorCharm(TypedCharmBase[CharmConfig]):
    """Charmed Operator for the PostgreSQL database."""

    config_type = CharmConfig
    on: "CharmEvents" = AuthorisationRulesChangeCharmEvents()

    def __init__(self, *args):
        super().__init__(*args)

        # Show logger name (module name) in logs
        root_logger = logging.getLogger()
        for handler in root_logger.handlers:
            if isinstance(handler, JujuLogHandler):
                handler.setFormatter(logging.Formatter("{name}:{message}", style="{"))

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
        self._unit = self.model.unit.name
        self._name = self.model.app.name
        self._namespace = self.model.name
        self._context = {"namespace": self._namespace, "app_name": self._name}
        self.cluster_name = f"patroni-{self._name}"

        self._observer = AuthorisationRulesObserver(self, "/usr/bin/juju-exec")
        self.framework.observe(self.on.databases_change, self._on_databases_change)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.leader_elected, self._on_leader_elected)
        self.framework.observe(self.on[PEER].relation_changed, self._on_peer_relation_changed)
        self.framework.observe(self.on.secret_changed, self._on_peer_relation_changed)
        # add specific handler for updated system-user secrets
        self.framework.observe(self.on.secret_changed, self._on_secret_changed)
        self.framework.observe(self.on[PEER].relation_departed, self._on_peer_relation_departed)
        self.framework.observe(self.on.start, self._on_start)
        self.framework.observe(self.on.postgresql_pebble_ready, self._on_postgresql_pebble_ready)
        self.framework.observe(self.on.data_storage_detaching, self._on_pgdata_storage_detaching)
        self.framework.observe(self.on.stop, self._on_stop)
        self.framework.observe(self.on.promote_to_primary_action, self._on_promote_to_primary)
        self.framework.observe(self.on.get_primary_action, self._on_get_primary)
        self.framework.observe(self.on.update_status, self._on_update_status)
        # Do not use collect status events elsewhere—otherwise ops will prioritize statuses incorrectly
        # https://canonical-charm-refresh.readthedocs-hosted.com/latest/add-to-charm/status/#implementation
        self.framework.observe(self.on.collect_unit_status, self._reconcile_refresh_status)
        self.framework.observe(self.on.secret_remove, self._on_secret_remove)

        self._certs_path = "/usr/local/share/ca-certificates"
        self._storage_path = str(self.meta.storages["data"].location)
        self.pgdata_path = f"{self._storage_path}/pgdata"

        self.framework.observe(self.on.upgrade_charm, self._on_upgrade_charm)
        self.postgresql_client_relation = PostgreSQLProvider(self)
        self.backup = PostgreSQLBackups(self, "s3-parameters")
        self.ldap = PostgreSQLLDAP(self, "ldap")
        self.tls = TLS(self, PEER)
        self.tls_transfer = TLSTransfer(self, PEER)
        self.async_replication = PostgreSQLAsyncReplication(self)
        self.logical_replication = PostgreSQLLogicalReplication(self)
        self.restart_manager = RollingOpsManager(
            charm=self, relation="restart", callback=self._restart
        )

        if self.model.juju_version.supports_open_port_on_k8s:
            try:
                self.unit.set_ports(5432, 8008)
            except ModelError:
                logger.exception("failed to open port")

        self.can_set_app_status = True
        try:
            self.refresh = charm_refresh.Kubernetes(
                PostgreSQLRefresh(
                    workload_name="PostgreSQL",
                    charm_name="postgresql-k8s",
                    oci_resource_name="postgresql-image",
                    _charm=self,
                )
            )
        except charm_refresh.KubernetesJujuAppNotTrusted:
            self.refresh = None
            self.can_set_app_status = False
        except charm_refresh.PeerRelationNotReady:
            self.refresh = None
        except charm_refresh.UnitTearingDown:
            self.unit.status = MaintenanceStatus("Tearing down")
            sys.exit()
        self._reconcile_refresh_status()

        # Support for disabling the operator.
        disable_file = Path(f"{os.environ.get('CHARM_DIR')}/disable")
        if disable_file.exists():
            logger.warning(
                f"\n\tDisable file `{disable_file.resolve()}` found, the charm will skip all events."
                "\n\tTo resume normal operations, please remove the file."
            )
            self.set_unit_status(BlockedStatus("Disabled"))
            sys.exit(0)

        if (
            self.refresh is not None
            and self.refresh.workload_allowed_to_start
            and not self.refresh.next_unit_allowed_to_refresh
        ):
            if self.refresh.in_progress:
                self.reconcile()
            else:
                self.refresh.next_unit_allowed_to_refresh = True

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
        self.tracing = TracingEndpointRequirer(
            self, relation_name=TRACING_RELATION_NAME, protocols=[TRACING_PROTOCOL]
        )

    def reconcile(self):
        """Reconcile the unit state on refresh."""
        self.set_unit_status(MaintenanceStatus("starting services"))
        self._update_pebble_layers(replan=True)

        if not self._patroni.member_started:
            logger.debug("Early exit reconcile: Patroni has not started yet")
            return

        if self.unit.is_leader() and not self._patroni.primary_endpoint_ready:
            logger.debug(
                "Early exit reconcile: current unit is leader but primary endpoint is not ready yet"
            )
            return

        self.set_unit_status(WaitingStatus("waiting for database initialisation"))
        try:
            for attempt in Retrying(stop=stop_after_attempt(6), wait=wait_fixed(10)):
                with attempt:
                    if not (
                        self.unit.name.replace("/", "-") in self._patroni.cluster_members
                        and self._patroni.is_replication_healthy
                    ):
                        logger.error(
                            "Instance not yet back in the cluster or not healthy."
                            f" Retry {attempt.retry_state.attempt_number}/6"
                        )
                        raise Exception
        except RetryError:
            logger.debug("Upgraded unit is not part of the cluster or not healthy")
            self.set_unit_status(
                BlockedStatus("upgrade failed. Check logs for rollback instruction")
            )
        else:
            if self.refresh is not None:
                self.refresh.next_unit_allowed_to_refresh = True
                self.set_unit_status(ActiveStatus())

    def _reconcile_refresh_status(self, _=None):
        if self.unit.is_leader():
            self.async_replication.set_app_status()

        # Workaround for other unit statuses being set in a stateful way (i.e. unable to recompute
        # status on every event)
        path = pathlib.Path(".last_refresh_unit_status.json")
        try:
            last_refresh_unit_status = json.loads(path.read_text())
        except FileNotFoundError:
            last_refresh_unit_status = None
        new_refresh_unit_status = None
        if self.refresh is not None and self.refresh.unit_status_higher_priority:
            self.unit.status = self.refresh.unit_status_higher_priority
            new_refresh_unit_status = self.refresh.unit_status_higher_priority.message
        elif self.unit.status.message == last_refresh_unit_status:
            if self.refresh is not None and (
                refresh_status := self.refresh.unit_status_lower_priority(
                    workload_is_running=self._is_workload_running
                )
            ):
                self.unit.status = refresh_status
                new_refresh_unit_status = refresh_status.message
            else:
                # Clear refresh status from unit status
                self._set_active_status()
        elif (
            isinstance(self.unit.status, ActiveStatus)
            and self.refresh is not None
            and (
                refresh_status := self.refresh.unit_status_lower_priority(
                    workload_is_running=self._is_workload_running
                )
            )
        ):
            self.unit.status = refresh_status
            new_refresh_unit_status = refresh_status.message
        path.write_text(json.dumps(new_refresh_unit_status))

    def set_unit_status(
        self, status: StatusBase, /, *, refresh: charm_refresh.Kubernetes | None = None
    ):
        """Set unit status without overriding higher priority refresh status."""
        if refresh is None:
            refresh = getattr(self, "refresh", None)
        if refresh is not None and refresh.unit_status_higher_priority:
            return
        if (
            isinstance(status, ActiveStatus)
            and refresh is not None
            and (refresh_status := refresh.unit_status_lower_priority())
        ):
            self.unit.status = refresh_status
            pathlib.Path(".last_refresh_unit_status.json").write_text(
                json.dumps(refresh_status.message)
            )
            return
        self.unit.status = status

    def _on_databases_change(self, _):
        """Handle databases change event."""
        self.update_config()
        logger.debug("databases changed")
        timestamp = datetime.now()
        self.unit_peer_data.update({"pg_hba_needs_update_timestamp": str(timestamp)})
        logger.debug(f"authorisation rules changed at {timestamp}")

    @property
    def tracing_endpoint(self) -> str | None:
        """Otlp http endpoint for charm instrumentation."""
        if self.tracing.is_ready():
            return self.tracing.get_endpoint(TRACING_PROTOCOL)

    def _generate_metrics_jobs(self, enable_tls: bool) -> list[dict]:
        """Generate spec for Prometheus scraping."""
        return [
            {"static_configs": [{"targets": [f"*:{METRICS_PORT}"]}]},
            {
                "static_configs": [{"targets": ["*:8008"]}],
                "scheme": "https",
                "tls_config": {"insecure_skip_verify": True},
            },
        ]

    @property
    def app_peer_data(self) -> dict:
        """Application peer relation data object."""
        return self.all_peer_data.get(self.app, {})

    @property
    def unit_peer_data(self) -> dict:
        """Unit peer relation data object."""
        return self.all_peer_data.get(self.unit, {})

    @property
    def all_peer_data(self) -> dict:
        """Return all peer data if available."""
        if self._peers is None:
            return {}

        # RelationData has dict like API
        return self._peers.data  # type: ignore

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
        self.peer_relation_data(scope).set_secret(peers.id, secret_key, value)

    def remove_secret(self, scope: Scopes, key: str) -> None:
        """Removing a secret."""
        if scope not in get_args(Scopes):
            raise RuntimeError("Unknown secret scope.")

        if not (peers := self.model.get_relation(PEER)):
            return None

        secret_key = self._translate_field_to_secret_key(key)

        self.peer_relation_data(scope).delete_relation_data(peers.id, [secret_key])

    def get_secret_from_id(self, secret_id: str) -> dict[str, str]:
        """Resolve the given id of a Juju secret and return the content as a dict.

        This method can be used to retrieve any secret, not just those used via the peer relation.
        If the secret is not owned by the charm, it has to be granted access to it.

        Args:
            secret_id (str): The id of the secret.

        Returns:
            dict: The content of the secret.
        """
        try:
            secret_content = self.model.get_secret(id=secret_id).get_content(refresh=True)
        except (SecretNotFoundError, ModelError):
            raise

        return secret_content

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
    def _container(self) -> Container:
        """Returns the postgresql container."""
        return self.unit.get_container("postgresql")

    @cached_property
    def postgresql(self) -> PostgreSQL:
        """Returns an instance of the object used to interact with the database."""
        return PostgreSQL(
            substrate=Substrates.K8S,
            primary_host=self.primary_endpoint,
            current_host=self.endpoint,
            user=USER,
            password=str(self.get_secret(APP_SCOPE, f"{USER}-password")),
            database=DATABASE_DEFAULT_NAME,
            system_users=SYSTEM_USERS,
        )

    @property
    def endpoint(self) -> str:
        """Returns the endpoint of this instance's pod."""
        return f"{self._unit.replace('/', '-')}.{self._build_service_name('endpoints')}"

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
            if binding := self.model.get_binding(PEER):
                return str(binding.network.bind_address)
        # Check if host is a peer.
        elif unit in self.all_peer_data and (
            addr := self.all_peer_data[unit].get("private-address")
        ):
            return str(addr)
        # Return None if the unit is not a peer neither the current unit.
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
        self.postgresql_client_relation.update_endpoints()
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

        if not primary:
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
            # Trigger the switchover.
            self._patroni.switchover()

            # Wait for the switchover to complete.
            self._patroni.primary_changed(primary)

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
        self.postgresql_client_relation.update_endpoints()
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
        if not self._container.can_connect():
            logger.debug(
                "Early exit on_peer_relation_changed: Waiting for container to become available"
            )
            return
        try:
            self.update_config()
        except ValueError as e:
            self.set_unit_status(BlockedStatus("Configuration Error. Please check the logs"))
            logger.error("Invalid configuration: %s", str(e))
            return

        # Should not override a blocked status
        if isinstance(self.unit.status, BlockedStatus):
            logger.debug("on_peer_relation_changed early exit: Unit in blocked status")
            return

        services = self._container.pebble.get_services(names=[self.postgresql_service])
        if (
            (self.is_cluster_restoring_backup or self.is_cluster_restoring_to_time)
            and len(services) > 0
            and not self._was_restore_successful(self._container, services[0])
        ):
            logger.debug("on_peer_relation_changed early exit: Backup restore check failed")
            return

        # Validate the status of the member before setting an ActiveStatus.
        if not self._patroni.member_started:
            logger.debug("Deferring on_peer_relation_changed: Waiting for member to start")
            self.set_unit_status(WaitingStatus("awaiting for member to start"))
            event.defer()
            return

        try:
            self.postgresql_client_relation.update_endpoints()
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

    def _on_secret_changed(self, event: SecretChangedEvent) -> None:
        """Handle the secret_changed event."""
        if not self.unit.is_leader():
            return

        if (admin_secret_id := self.config.system_users) and admin_secret_id == event.secret.id:
            try:
                self._update_admin_password(admin_secret_id)
            except PostgreSQLUpdateUserPasswordError:
                event.defer()

    def _on_config_changed(self, event) -> None:
        """Handle configuration changes, like enabling plugins."""
        if not self.is_cluster_initialised:
            logger.debug("Defer on_config_changed: cluster not initialised yet")
            event.defer()
            return

        if self.refresh is None:
            logger.warning("Warning _on_config_changed: Refresh could be in progress")
        elif self.refresh.in_progress:
            logger.debug("Defer on_config_changed: Refresh in progress")
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
            self.set_unit_status(BlockedStatus("Configuration Error. Please check the logs"))
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

        if not self.logical_replication.apply_changed_config(event):
            return

        if not self.unit.is_leader():
            return

        # Enable and/or disable the extensions.
        self.enable_disable_extensions()

        if admin_secret_id := self.config.system_users:
            try:
                self._update_admin_password(admin_secret_id)
            except PostgreSQLUpdateUserPasswordError:
                event.defer()

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
                self.set_unit_status(BlockedStatus(EXTENSIONS_DEPENDENCY_MESSAGE))
                return
            extensions[extension] = enable
        if self.is_blocked and self.unit.status.message == EXTENSIONS_DEPENDENCY_MESSAGE:
            self._set_active_status()
            original_status = self.unit.status

        self._handle_enable_disable_extensions(original_status, extensions, database)

    def _handle_enable_disable_extensions(self, original_status, extensions, database) -> None:
        """Try enablind/disabling Postgresql extensions and handle exceptions appropriately."""
        if not isinstance(original_status, UnknownStatus):
            self.set_unit_status(WaitingStatus("Updating extensions"))
        try:
            self.postgresql.enable_disable_extensions(extensions, database)
        except psycopg2.errors.DependentObjectsStillExist as e:
            logger.error(
                "Failed to disable plugin: %s\nWas the plugin enabled manually? If so, update charm config with `juju config postgresql-k8s plugin_<plugin_name>_enable=True`",
                str(e),
            )
            self.set_unit_status(BlockedStatus(EXTENSION_OBJECT_MESSAGE))
            return
        except PostgreSQLEnableDisableExtensionError as e:
            logger.exception("failed to change plugins: %s", str(e))
        if original_status.message == EXTENSION_OBJECT_MESSAGE:
            self._set_active_status()
            return
        if not isinstance(original_status, UnknownStatus):
            self.set_unit_status(original_status)

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
            self.set_unit_status(MaintenanceStatus("reconfiguring cluster"))
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
            self.set_unit_status(BlockedStatus(f"failed to patch pod with error {e}"))
            return

    @property
    def _hosts(self) -> set:
        """List of the current Juju hosts.

        Returns:
            a set containing the current Juju hosts
                with the names in the k8s pod name format
        """
        hosts = [self._unit_name_to_pod_name(self.unit.name)]
        if self._peers:
            for unit in self._peers.units:
                hosts.append(self._unit_name_to_pod_name(unit.name))
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

    def _setup_passwords(self, event: LeaderElectedEvent) -> None:
        """Setup system users' passwords."""
        # consider configured system user passwords
        system_user_passwords = {}
        if admin_secret_id := self.config.system_users:
            try:
                system_user_passwords = self.get_secret_from_id(secret_id=admin_secret_id)
            except (ModelError, SecretNotFoundError) as e:
                # only display the error but don't return to make sure all users have passwords
                logger.error(f"Error setting internal passwords: {e}")
                self.set_unit_status(BlockedStatus("Password setting for system users failed."))
                event.defer()

        for password in {
            USER_PASSWORD_KEY,
            REPLICATION_PASSWORD_KEY,
            REWIND_PASSWORD_KEY,
            MONITORING_PASSWORD_KEY,
            PATRONI_PASSWORD_KEY,
        }:
            if self.get_secret(APP_SCOPE, password) is None:
                if password in system_user_passwords:
                    # use provided passwords for system-users if available
                    self.set_secret(APP_SCOPE, password, system_user_passwords[password])
                    logger.info(f"Using configured password for {password}")
                else:
                    # generate a password for this user if not provided
                    self.set_secret(APP_SCOPE, password, new_password())
                    logger.info(f"Generated new password for {password}")

    def _on_leader_elected(self, event: LeaderElectedEvent) -> None:
        """Handle the leader-elected event."""
        self._setup_passwords(event)

        # Add this unit to the list of cluster members
        # (the cluster should start with only this member).
        if self._endpoint not in self._endpoints:
            self._add_to_endpoints(self._endpoint)

        if not self.get_secret(APP_SCOPE, "internal-ca"):
            self.tls.generate_internal_peer_ca()

        self._cleanup_old_cluster_resources()

        if not self.fix_leader_annotation():
            return

        # Create resources and add labels needed for replication.
        if self.refresh is not None and not self.refresh.in_progress:
            try:
                self._create_services()
            except ApiError:
                logger.exception("failed to create k8s services")
                self.set_unit_status(BlockedStatus("failed to create k8s services"))
                return

        # Remove departing units when the leader changes.
        self._remove_from_endpoints(self._get_endpoints_to_remove())

        self._add_members(event)

    def fix_leader_annotation(self) -> bool:
        """Fix the leader annotation if it's missing."""
        client = Client()
        try:
            endpoint = client.get(Endpoints, name=self.cluster_name, namespace=self._namespace)
            if (
                endpoint.metadata
                and endpoint.metadata.annotations is not None
                and "leader" not in endpoint.metadata.annotations
            ):
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
                self.pgdata_path, permissions=0o700, user=WORKLOAD_OS_USER, group=WORKLOAD_OS_GROUP
            )
        # Also, fix the permissions from the parent directory.
        container.exec([
            "chown",
            f"{WORKLOAD_OS_USER}:{WORKLOAD_OS_GROUP}",
            "/var/lib/postgresql/archive",
        ]).wait()
        container.exec([
            "chown",
            f"{WORKLOAD_OS_USER}:{WORKLOAD_OS_GROUP}",
            self._storage_path,
        ]).wait()
        container.exec([
            "chown",
            f"{WORKLOAD_OS_USER}:{WORKLOAD_OS_GROUP}",
            "/var/lib/postgresql/logs",
        ]).wait()
        container.exec([
            "chown",
            f"{WORKLOAD_OS_USER}:{WORKLOAD_OS_GROUP}",
            "/var/lib/postgresql/temp",
        ]).wait()

    def _on_start(self, _) -> None:
        # Make sure the CA bubdle file exists
        # Bundle is not secret
        Path(f"/tmp/{TLS_CA_BUNDLE_FILE}").touch()  # noqa: S108

    def _on_postgresql_pebble_ready(self, event: WorkloadEvent) -> None:
        """Event handler for PostgreSQL container on PebbleReadyEvent."""
        # Safeguard against starting while refreshing.
        if self.refresh is None:
            logger.warning("Warning on_postgresql_pebble_ready: Refresh could be in progress")
        elif self.refresh.in_progress and not self.refresh.workload_allowed_to_start:
            logger.debug("Defer on_postgresql_pebble_ready: Refresh in progress")
            event.defer()
            return

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

        if not self.get_secret(APP_SCOPE, "internal-ca"):
            logger.info("leader not elected and/or internal CA not yet generated")
            event.defer()
            return
        if not self.get_secret(UNIT_SCOPE, "internal-cert"):
            self.tls.generate_internal_peer_cert()

        try:
            for ca_secret_name in self.tls_transfer.get_ca_secret_names():
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
            self.set_unit_status(WaitingStatus("awaiting for cluster to start"))
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
                self.set_unit_status(
                    BlockedStatus(self.app_peer_data["s3-initialization-block-message"])
                )
                return
            if self.unit.is_leader() and (
                self.app_peer_data.get("logical-replication-validation") == "error"
                or self.logical_replication.has_remote_publisher_errors()
            ):
                self.set_unit_status(BlockedStatus(LOGICAL_REPLICATION_VALIDATION_ERROR_STATUS))
                return
            if (
                self._patroni.get_primary(unit_name_pattern=True) == self.unit.name
                or self.is_standby_leader
            ):
                danger_state = ""
                if len(self._patroni.get_running_cluster_members()) < self.app.planned_units():
                    danger_state = " (degraded)"
                self.set_unit_status(
                    ActiveStatus(
                        f"{'Standby' if self.is_standby_leader else 'Primary'}{danger_state}"
                    )
                )
            elif self._patroni.member_started:
                self.set_unit_status(ActiveStatus())
        except (RetryError, RequestsConnectionError) as e:
            logger.error(f"failed to get primary with error {e}")

    def _initialize_cluster(self, event: HookEvent) -> bool:
        # Add the labels needed for replication in this pod.
        # This also enables the member as part of the cluster.
        try:
            self._patch_pod_labels(self._unit)
        except ApiError as e:
            logger.error("failed to patch pod")
            self.set_unit_status(BlockedStatus(f"failed to patch pod with error {e}"))
            return False

        # Create resources and add labels needed for replication
        if self.refresh is not None and not self.refresh.in_progress:
            try:
                self._create_services()
            except ApiError:
                logger.exception("failed to create k8s services")
                self.set_unit_status(BlockedStatus("failed to create k8s services"))
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
            self.set_unit_status(WaitingStatus("awaiting for primary endpoint to be ready"))
            event.defer()
            return False

        try:
            self._setup_users()
        except PostgreSQLCreatePredefinedRolesError:
            message = "Failed to create pre-defined roles"
            logger.exception(message)
            self.set_unit_status(BlockedStatus(message))
            return False
        except PostgreSQLGrantDatabasePrivilegesToUserError:
            message = "Failed to grant database privileges to user"
            logger.exception(message)
            self.set_unit_status(BlockedStatus(message))
            return False
        except PostgreSQLCreateUserError:
            message = "Failed to create postgres user"
            logger.exception(message)
            self.set_unit_status(BlockedStatus(message))
            return False
        except PostgreSQLListUsersError:
            logger.warning("Deferring on_start: Unable to list users")
            event.defer()
            return False

        # Mark the cluster as initialised.
        self.app_peer_data["cluster_initialised"] = "True"

        return True

    def _setup_users(self) -> None:
        self.postgresql.create_predefined_instance_roles()

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

        self.postgresql.set_up_database(temp_location="/var/lib/postgresql/temp")

        access_groups = self.postgresql.list_access_groups()
        if access_groups != set(ACCESS_GROUPS):
            self.postgresql.create_access_groups()
            self.postgresql.grant_internal_access_group_memberships()

    @property
    def is_blocked(self) -> bool:
        """Returns whether the unit is in a blocked state."""
        return isinstance(self.unit.status, BlockedStatus)

    def _on_upgrade_charm(self, event) -> None:
        try:
            self._fix_pod()
        except Exception as e:
            logger.debug(f"Defer _on_upgrade charm: failed to fix pod: {e}")
            event.defer()

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
        if not pod0 or not pod0.metadata:
            raise Exception("Unable to get pod0")

        services = {
            "primary": "primary",
            "replicas": "replica",
        }
        for service_name_suffix, role_selector in services.items():
            name = f"{self._name}-{service_name_suffix}"
            service = Service(
                metadata=ObjectMeta(
                    name=name,
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
                obj=service,  # type: ignore
                name=name,
                namespace=self.model.name,
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

    def _update_admin_password(self, admin_secret_id: str) -> None:
        """Check if the password of a system user was changed and update it in the database."""
        if not self._patroni.are_all_members_ready():
            # Ensure all members are ready before reloading Patroni configuration to avoid errors
            # e.g. API not responding in one instance because PostgreSQL / Patroni are not ready
            raise PostgreSQLUpdateUserPasswordError(
                "Failed changing the password: Not all members healthy or finished initial sync."
            )

        # cross-cluster replication: extract the database host on which to update the passwords
        replication_offer_relation = self.model.get_relation(REPLICATION_OFFER_RELATION)
        other_cluster_primary_ip = ""
        if (
            replication_offer_relation is not None
            and not self.async_replication.is_primary_cluster()
        ):
            other_cluster_endpoints = self.async_replication.get_all_primary_cluster_endpoints()
            other_cluster_primary = self._patroni.get_primary(
                alternative_endpoints=other_cluster_endpoints
            )
            other_cluster_primary_ip = next(
                replication_offer_relation.data[unit].get("private-address")
                for unit in replication_offer_relation.units
                if unit.name.replace("/", "-") == other_cluster_primary
            )
        elif self.model.get_relation(REPLICATION_CONSUMER_RELATION) is not None:
            logger.error(
                "Failed changing the password: This can be ran only in the cluster from the offer side."
            )
            self.set_unit_status(BlockedStatus("Password update for system users failed."))
            return

        try:
            # get the secret content and check each user configured there
            # only SYSTEM_USERS with changed passwords are processed, all others ignored
            updated_passwords = self.get_secret_from_id(secret_id=admin_secret_id)
            for user, password in list(updated_passwords.items()):
                if user not in SYSTEM_USERS:
                    logger.error(
                        f"Can only update system users: {', '.join(SYSTEM_USERS)} not {user}"
                    )
                    updated_passwords.pop(user)
                    continue
                if password == self.get_secret(APP_SCOPE, f"{user}-password"):
                    updated_passwords.pop(user)
        except (ModelError, SecretNotFoundError) as e:
            logger.error(f"Error updating internal passwords: {e}")
            self.set_unit_status(BlockedStatus("Password update for system users failed."))
            return

        try:
            # perform the actual password update for the remaining users
            for user, password in updated_passwords.items():
                logger.info(f"Updating password for user {user}")
                self.postgresql.update_user_password(
                    user,
                    password,
                    database_host=other_cluster_primary_ip if other_cluster_primary_ip else None,
                )
                # Update the password in the secret store after updating it in the database
                self.set_secret(APP_SCOPE, f"{user}-password", password)
        except PostgreSQLUpdateUserPasswordError as e:
            logger.exception(e)
            self.set_unit_status(BlockedStatus("Password update for system users failed."))
            return

        # Update and reload Patroni configuration in this unit to use the new password.
        # Other units Patroni configuration will be reloaded in the peer relation changed event.
        self.update_config()

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
        self.push_tls_files_to_workload()
        if self.refresh is not None and not self.refresh.in_progress:
            try:
                self._create_services()
            except ApiError:
                logger.exception("failed to create k8s services")
                self.set_unit_status(BlockedStatus("failed to create k8s services"))
                return

        try:
            self._patch_pod_labels(self.unit.name)
        except ApiError as e:
            logger.error("failed to patch pod")
            self.set_unit_status(BlockedStatus(f"failed to patch pod with error {e}"))
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

        if not pod0 or not pod0.metadata:
            logger.error("Failed to get pod0 details")
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
                not resource.metadata
                or not resource.metadata.name
                or not resource.metadata.namespace
                or (
                    type(resource) is Service
                    and resource.metadata.name
                    in [
                        self._name,
                        f"{self._name}-endpoints",
                        f"{self._name}-primary",
                        f"{self._name}-replicas",
                    ]
                )
                or resource.metadata.ownerReferences == pod0.metadata.ownerReferences
            ):
                continue
            # Patch the resource.
            try:
                resource.metadata.ownerReferences = pod0.metadata.ownerReferences
                resource.metadata.managedFields = None
                client.apply(
                    obj=resource,  # type: ignore
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
        if self.refresh is None:
            logger.debug("Early exit on_update_status: Refresh could be in progress")
            return False
        if self.refresh.in_progress:
            logger.debug("Early exit on_update_status: Refresh in progress")
            return False

        if not container.can_connect():
            logger.debug("on_update_status early exit: Cannot connect to container")
            return False

        self._check_pgdata_storage_size()

        if (
            self._has_blocked_status
            and self.unit.status not in S3_BLOCK_MESSAGES
            and self.unit.status.message != LOGICAL_REPLICATION_VALIDATION_ERROR_STATUS
        ) or self._has_non_restore_waiting_status:
            # If charm was failing to disable plugin, try again and continue (user may have removed the objects)
            if self.unit.status.message == EXTENSION_OBJECT_MESSAGE:
                self.enable_disable_extensions()
                return True

            logger.error("calling self.fix_leader_annotation()")
            self.fix_leader_annotation()

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
            self.set_unit_status(BlockedStatus(INSUFFICIENT_SIZE_WARNING))
        elif self.unit.status.message == INSUFFICIENT_SIZE_WARNING:
            self.set_unit_status(ActiveStatus())
            self._set_active_status()

    def _on_update_status(self, _) -> None:
        """Update the unit status message."""
        if not self._on_update_status_early_exit_checks(self._container):
            return

        services = self._container.pebble.get_services(names=[self.postgresql_service])
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
                self._container.restart(self.postgresql_service)
            except ChangeError:
                logger.exception("Failed to restart patroni")
            # If service doesn't recover fast, exit and wait for next hook run to re-check
            if not self._patroni.member_started:
                self.set_unit_status(MaintenanceStatus("Database service inactive, restarting"))
                return

        if (
            self.is_cluster_restoring_backup or self.is_cluster_restoring_to_time
        ) and not self._was_restore_successful(self._container, services[0]):
            return

        # Update the sync-standby endpoint in the async replication data.
        self.async_replication.update_async_replication_data()

        self.backup.coordinate_stanza_fields()

        self.logical_replication.retry_validations()

        self._set_active_status()

    def _was_restore_successful(self, container: Container, service: ServiceInfo) -> bool:
        """Checks if restore operation succeeded and S3 is properly configured."""
        if self.is_cluster_restoring_to_time and all(self.is_pitr_failed(container)):
            logger.error(
                "Restore failed: database service failed to reach point-in-time-recovery target. "
                "You can launch another restore with different parameters"
            )
            self.log_pitr_last_transaction_time()
            self.set_unit_status(BlockedStatus(CANNOT_RESTORE_PITR))
            return False

        if (
            service.current != ServiceStatus.ACTIVE
            and self.unit.status.message != CANNOT_RESTORE_PITR
        ):
            logger.error("Restore failed: database service failed to start")
            self.set_unit_status(BlockedStatus("Failed to restore backup"))
            return False

        if not self._patroni.member_started:
            logger.debug("Restore check early exit: Patroni has not started yet")
            return False

        try:
            self._setup_users()
        except Exception:
            logger.exception("Failed to set up users after restore")
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
        return all(self.tls.get_client_tls_files())

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

        endpoints = json.loads(self.app_peer_data.get("endpoints", "[]"))
        if endpoint_to_add:
            endpoints.append(endpoint_to_add)
        elif endpoints_to_remove:
            for endpoint in endpoints_to_remove:
                endpoints.remove(endpoint)
        self.app_peer_data["endpoints"] = json.dumps(endpoints)

    def _generate_ldap_service(self) -> ServiceDict:
        """Generate the LDAP service definition."""
        ldap_params = self.get_ldap_parameters()

        ldap_url = urlparse(ldap_params["ldapurl"])
        ldap_host = ldap_url.hostname
        ldap_port = ldap_url.port

        ldap_base_dn = ldap_params["ldapbasedn"]
        ldap_bind_username = ldap_params["ldapbinddn"]
        ldap_bind_password = ldap_params["ldapbindpasswd"]
        ldap_group_mappings = self.postgresql.build_postgresql_group_map(self.config.ldap_map)

        if not (password := self.get_secret(APP_SCOPE, USER_PASSWORD_KEY)):
            raise Exception("No password generated yet")

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
                "POSTGRES_PASSWORD": password,
            },
        }

    def _generate_metrics_service(self) -> ServiceDict:
        """Generate the metrics service definition."""
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

    def _postgresql_layer(self) -> Layer:
        """Returns a Pebble configuration layer for PostgreSQL."""
        pod_name = self._unit_name_to_pod_name(self._unit)
        layer_config = LayerDict({
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
                    "exec": {
                        "command": "python3 /scripts/self-signed-checker.py",
                        "user": WORKLOAD_OS_USER,
                        "environment": {
                            "ENDPOINT": f"{self._patroni._patroni_url}/health",
                        },
                    },
                }
            },
        })
        return Layer(layer_config)

    @property
    def _peers(self) -> Relation | None:
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
        key, ca, cert = self.tls.get_client_tls_files()
        if key is not None:
            self._push_file_to_workload(
                self._container, f"{self._storage_path}/{TLS_KEY_FILE}", key
            )
        if ca is not None:
            self._push_file_to_workload(self._container, f"{self._storage_path}/{TLS_CA_FILE}", ca)
            self._push_file_to_workload(self._container, f"{self._certs_path}/ca.crt", ca)
            self._container.exec(["update-ca-certificates"]).wait()
        if cert is not None:
            self._push_file_to_workload(
                self._container, f"{self._storage_path}/{TLS_CERT_FILE}", cert
            )

        key, ca, cert = self.tls.get_peer_tls_files()
        if key is not None:
            self._push_file_to_workload(
                self._container, f"{self._storage_path}/peer_{TLS_KEY_FILE}", key
            )
        if ca is not None:
            self._push_file_to_workload(
                self._container, f"{self._storage_path}/peer_{TLS_CA_FILE}", ca
            )
        if cert is not None:
            self._push_file_to_workload(
                self._container, f"{self._storage_path}/peer_{TLS_CERT_FILE}", cert
            )

        # CA bundle is not secret
        with open(f"/tmp/{TLS_CA_BUNDLE_FILE}", "w") as fp:  # noqa: S108
            fp.write(self.tls.get_peer_ca_bundle())

        return self.update_config()

    def push_ca_file_into_workload(self, secret_name: str) -> bool:
        """Uploads CA certificate into the workload container."""
        certificates = self.get_secret(UNIT_SCOPE, secret_name)

        if certificates is not None:
            self._push_file_to_workload(
                container=self._container,
                file_path=f"{self._certs_path}/{secret_name}.crt",
                file_data=certificates,
            )
            self._container.exec(["update-ca-certificates"]).wait()

        return self.update_config()

    def clean_ca_file_from_workload(self, secret_name: str) -> bool:
        """Cleans up CA certificate from the workload container."""
        self._container.remove_path(f"{self._certs_path}/{secret_name}.crt")
        self._container.exec(["update-ca-certificates"]).wait()

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
            self.set_unit_status(BlockedStatus(error_message))
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
        current_layer = self._container.get_plan()

        metrics_service = current_layer.services[self.metrics_service]
        data_source_name = metrics_service.environment.get("DATA_SOURCE_NAME", "")

        if metrics_service and not data_source_name.startswith(
            f"user={MONITORING_USER} password={self.get_secret('app', MONITORING_PASSWORD_KEY)} "
        ):
            self._container.add_layer(
                self.metrics_service,
                Layer({"services": {self.metrics_service: self._generate_metrics_service()}}),
                combine=True,
            )
            self._container.restart(self.metrics_service)

    def _restart_ldap_sync_service(self) -> None:
        """Restart the LDAP sync service in case any configuration changed."""
        if not self._patroni.member_started:
            logger.debug("Restart LDAP sync early exit: Patroni has not started yet")
            return

        sync_service = self._container.pebble.get_services(names=[self.ldap_sync_service])

        if not self.is_primary and sync_service[0].is_running():
            logger.debug("Stopping LDAP sync service. It must only run in the primary")
            self._container.stop(self.ldap_sync_service)

        if self.is_primary and not self.is_ldap_enabled:
            logger.debug("Stopping LDAP sync service")
            self._container.stop(self.ldap_sync_service)
            return

        if self.is_primary and self.is_ldap_enabled:
            self._container.add_layer(
                self.ldap_sync_service,
                Layer({"services": {self.ldap_sync_service: self._generate_ldap_service()}}),
                combine=True,
            )
            logger.debug("Starting LDAP sync service")
            self._container.restart(self.ldap_sync_service)

    @property
    def _is_workload_running(self) -> bool:
        """Returns whether the workload is running (in an active state)."""
        if not self._container.can_connect():
            return False

        services = self._container.pebble.get_services(names=[self.postgresql_service])
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

    def _api_update_config(self, available_cpu_cores: int) -> None:
        # Use config value if set, calculate otherwise
        if self.config.experimental_max_connections:
            max_connections = self.config.experimental_max_connections
        else:
            max_connections = max(4 * available_cpu_cores, 100)

        cfg_patch = {
            "max_connections": max_connections,
            "max_prepared_transactions": self.config.memory_max_prepared_transactions,
            "max_replication_slots": 25,
            "max_wal_senders": 25,
            "shared_buffers": self.config.memory_shared_buffers,
            "wal_keep_size": self.config.durability_wal_keep_size,
        }
        base_patch = {}
        if primary_endpoint := self.async_replication.get_primary_cluster_endpoint():
            base_patch["standby_cluster"] = {"host": primary_endpoint}
        self._patroni.bulk_update_parameters_controller_by_patroni(cfg_patch, base_patch)

    def update_config(self, is_creating_backup: bool = False) -> bool:
        """Updates Patroni config file based on the existence of the TLS files."""
        # Retrieve PostgreSQL parameters.
        if self.config.profile_limit_memory:
            limit_memory = self.config.profile_limit_memory * 10**6
        else:
            limit_memory = None
        try:
            available_cpu_cores, available_memory = self.get_available_resources()
        except ApiError as e:
            if e.status.code == 403:
                self.on_deployed_without_trust()
                return False
            raise e

        # TODO Updating the lib should accept ConfigData
        postgresql_parameters = self.postgresql.build_postgresql_parameters(
            self.model.config,  # type: ignore
            available_memory,
            limit_memory,
        )

        replication_slots = self.logical_replication.replication_slots()

        logger.info("Updating Patroni config file")
        # Update and reload configuration based on TLS files availability.
        self._patroni.render_patroni_yml_file(
            connectivity=self.is_connectivity_enabled,
            is_creating_backup=is_creating_backup,
            enable_ldap=self.is_ldap_enabled,
            enable_tls=self.is_tls_enabled,
            backup_id=self.app_peer_data.get("restoring-backup"),
            pitr_target=self.app_peer_data.get("restore-to-time"),
            restore_timeline=self.app_peer_data.get("restore-timeline"),
            restore_to_latest=self.app_peer_data.get("restore-to-time", None) == "latest",
            stanza=self.app_peer_data.get("stanza", self.unit_peer_data.get("stanza")),
            restore_stanza=self.app_peer_data.get("restore-stanza"),
            parameters=postgresql_parameters,
            user_databases_map=self.relations_user_databases_map,
            slots=replication_slots,
        )

        if not self._is_workload_running:
            # If Patroni/PostgreSQL has not started yet and TLS relations was initialised,
            # then mark TLS as enabled. This commonly happens when the charm is deployed
            # in a bundle together with the TLS certificates operator. This flag is used to
            # know when to call the Patroni API using HTTP or HTTPS.
            self.unit_peer_data.update({"tls": "enabled" if self.is_tls_enabled else ""})
            self.postgresql_client_relation.update_endpoints()
            logger.debug("Early exit update_config: Workload not started yet")
            return True

        if not self._patroni.member_started:
            if self.is_tls_enabled:
                logger.debug(
                    "Early exit update_config: patroni not responding but TLS is enabled."
                )
                self._handle_postgresql_restart_need(True)
                return True
            logger.debug("Early exit update_config: Patroni not started yet")
            return False

        self._api_update_config(available_cpu_cores)

        self._patroni.ensure_slots_controller_by_patroni(replication_slots)

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

        if self.config.request_date_style and not self.postgresql.validate_date_style(
            self.config.request_date_style
        ):
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

    def _handle_postgresql_restart_need(self, config_changed: bool):
        """Handle PostgreSQL restart need based on the TLS configuration and configuration changes."""
        if self._can_connect_to_postgresql:
            restart_postgresql = self.is_tls_enabled != self.postgresql.is_tls_enabled()
        else:
            restart_postgresql = False
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
                        restart_postgresql = restart_postgresql or self.is_restart_pending()
                        if not restart_postgresql:
                            raise Exception
            except RetryError:
                # Ignore the error, as it happens only to indicate that the configuration has not changed.
                pass

        self.unit_peer_data.update({"tls": "enabled" if self.is_tls_enabled else ""})
        self.postgresql_client_relation.update_endpoints()

        # Restart PostgreSQL if TLS configuration has changed
        # (so the both old and new connections use the configuration).
        if restart_postgresql:
            logger.info("PostgreSQL restart required")
            self.metrics_endpoint.update_scrape_job_spec(
                self._generate_metrics_jobs(self.is_tls_enabled)
            )
            self.on[str(self.restart_manager.name)].acquire_lock.emit()

    def _update_pebble_layers(self, replan: bool = True) -> None:
        """Update the pebble layers to keep the health check URL up-to-date."""
        # Get the current layer.
        current_layer = self._container.get_plan()

        # Create a new config layer.
        new_layer = self._postgresql_layer()

        # Check if there are any changes to layer services.
        if current_layer.services != new_layer.services:
            # Changes were made, add the new layer.
            self._container.add_layer(self.postgresql_service, new_layer, combine=True)
            logging.info("Added updated layer 'postgresql' to Pebble plan")
            if replan:
                self._container.replan()
                logging.info("Restarted postgresql service")
        if current_layer.checks != new_layer.checks:
            # Changes were made, add the new layer.
            self._container.add_layer(self.postgresql_service, new_layer, combine=True)
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
        if pod.spec and pod.spec.nodeName:
            return pod.spec.nodeName
        else:
            raise Exception("Pod doesn't exist")

    def get_resources_limits(self, container_name: str) -> dict:
        """Return resources limits for a given container.

        Args:
            container_name: name of the container to get resources limits for
        """
        client = Client()
        pod = client.get(
            Pod, self._unit_name_to_pod_name(self.unit.name), namespace=self._namespace
        )

        if pod.spec:
            for container in pod.spec.containers:
                if container.name == container_name and container.resources:
                    return container.resources.limits or {}
        return {}

    def get_node_allocable_memory(self) -> int:
        """Return the allocable memory in bytes for the current K8S node."""
        client = Client()
        node = client.get(Node, name=self._get_node_name_for_pod(), namespace=self._namespace)  # type: ignore
        return any_memory_to_bytes(node.status.allocatable["memory"])

    def get_node_cpu_cores(self) -> int:
        """Return the number of CPU cores for the current K8S node."""
        client = Client()
        node = client.get(Node, name=self._get_node_name_for_pod(), namespace=self._namespace)  # type: ignore
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
        self.set_unit_status(
            BlockedStatus(
                f"Insufficient permissions, try: `juju trust {self._name} --scope=cluster`"
            )
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
        return self.model.relations.get("database", [])

    @property
    def relations_user_databases_map(self) -> dict:
        """Returns a user->databases map for all relations."""
        try:
            if (
                not self.is_cluster_initialised
                or not self._patroni.member_started
                or self.postgresql.list_access_groups(current_host=self.is_connectivity_enabled)
                != set(ACCESS_GROUPS)
            ):
                return {USER: "all", REPLICATION_USER: "all", REWIND_USER: "all"}
        except PostgreSQLListGroupsError as e:
            logger.warning(f"Failed to list access groups: {e}")
            return {USER: "all", REPLICATION_USER: "all", REWIND_USER: "all"}

        user_database_map = self._collect_user_relations()
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
            if databases := ",".join(
                self.postgresql.list_accessible_databases_for_user(
                    user, current_host=self.is_connectivity_enabled
                )
            ):
                user_database_map[user] = databases
            else:
                logger.debug(f"User {user} has no databases to connect to")

        return user_database_map

    def _collect_user_relations(self) -> dict[str, str]:
        user_db_pairs = {}
        custom_username_mapping = self.postgresql_client_relation.get_username_mapping()
        prefix_database_mapping = self.postgresql_client_relation.get_databases_prefix_mapping()

        for relation in self.model.relations[self.postgresql_client_relation.relation_name]:
            if database := self.postgresql_client_relation.database_provides.fetch_relation_field(
                relation.id, "database"
            ):
                user = custom_username_mapping.get(str(relation.id), f"relation_id_{relation.id}")
                database = ",".join(prefix_database_mapping.get(str(relation.id), [database]))
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

    def is_restart_pending(self) -> bool:
        """Query pg_settings for pending restart."""
        connection = None
        try:
            with (
                self.postgresql._connect_to_database() as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute("SELECT COUNT(*) FROM pg_settings WHERE pending_restart=True;")
                result = cursor.fetchone()
                if result is not None:
                    return result[0] > 0
                else:
                    return False
        except psycopg2.OperationalError:
            logger.warning("Failed to connect to PostgreSQL.")
            return False
        except psycopg2.Error as e:
            logger.error(f"Failed to check if restart is pending: {e}")
            return False
        finally:
            if connection:
                connection.close()


if __name__ == "__main__":
    main(PostgresqlOperatorCharm, use_juju_for_storage=True)
