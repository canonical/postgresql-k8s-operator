#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helper class used to manage interactions with Patroni API and configuration files."""

import logging
import os
import pwd
from asyncio import as_completed, create_task, run, wait
from contextlib import suppress
from functools import cached_property
from signal import SIGHUP
from ssl import CERT_NONE, create_default_context
from typing import Any, TypedDict

import requests
import yaml
from httpx import AsyncClient, BasicAuth, HTTPError
from jinja2 import Template
from ops.pebble import Error
from tenacity import (
    Future,
    RetryError,
    Retrying,
    retry,
    retry_if_result,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential,
    wait_fixed,
)

from constants import (
    API_REQUEST_TIMEOUT,
    PATRONI_CLUSTER_STATUS_ENDPOINT,
    POSTGRESQL_LOGS_PATH,
    POSTGRESQL_LOGS_PATTERN,
    REWIND_USER,
    TLS_CA_FILE,
)
from utils import label2name

STARTED_STATES = ["running", "streaming"]
RUNNING_STATES = [*STARTED_STATES, "starting"]
PATRONI_TIMEOUT = 10

logger = logging.getLogger(__name__)


class ClusterNotPromotedError(Exception):
    """Raised when a cluster is not promoted."""


class NotReadyError(Exception):
    """Raised when not all cluster members healthy or finished initial sync."""


class EndpointNotReadyError(Exception):
    """Raised when an endpoint is not ready."""


class StandbyClusterAlreadyPromotedError(Exception):
    """Raised when a standby cluster is already promoted."""


class SwitchoverFailedError(Exception):
    """Raised when a switchover failed for some reason."""


class SwitchoverNotSyncError(SwitchoverFailedError):
    """Raised when a switchover failed because node is not sync."""


class UpdateSyncNodeCountError(Exception):
    """Raised when updating synchronous_node_count failed for some reason."""


class ClusterMember(TypedDict):
    """Type for cluster member."""

    name: str
    role: str
    state: str
    api_url: str
    host: str
    port: int
    timeline: int
    lag: int


class Patroni:
    """This class handles the communication with Patroni API and configuration files."""

    def __init__(
        self,
        charm,
        endpoint: str,
        endpoints: list[str],
        primary_endpoint: str,
        namespace: str,
        storage_path: str,
        superuser_password: str,
        replication_password: str,
        rewind_password: str,
        patroni_password: str,
    ):
        self._charm = charm
        self._endpoint = endpoint
        self._endpoints = endpoints
        self._primary_endpoint = primary_endpoint
        self._namespace = namespace
        self._storage_path = storage_path
        self._members_count = len(self._endpoints)
        self._superuser_password = superuser_password
        self._replication_password = replication_password
        self._rewind_password = rewind_password
        self._patroni_password = patroni_password

    @property
    def _verify(self) -> str | bool:
        # Variable mapping to requests library verify parameter.
        # The CA bundle file is used to validate the server certificate when
        # TLS is enabled, otherwise True is set because it's the default value.
        return f"{self._storage_path}/{TLS_CA_FILE}" if self._charm.is_peer_data_tls_set else True

    @cached_property
    def _patroni_auth(self) -> requests.auth.HTTPBasicAuth:
        return requests.auth.HTTPBasicAuth("patroni", self._patroni_password)

    @cached_property
    def _patroni_async_auth(self) -> BasicAuth | None:
        if self._patroni_password:
            return BasicAuth("patroni", password=self._patroni_password)

    @property
    def _patroni_url(self) -> str:
        """Patroni REST API URL."""
        return f"{'https' if self._charm.is_peer_data_tls_set else 'http'}://{self._endpoint}:8008"

    @property
    def rock_postgresql_version(self) -> str | None:
        """Version of Postgresql installed in the Rock image."""
        container = self._charm.unit.get_container("postgresql")
        if not container.can_connect():
            logger.debug("Cannot get Postgresql version from Rock. Container inaccessible")
            return
        snap_meta = container.pull("/meta.charmed-postgresql/snap.yaml")
        return yaml.safe_load(snap_meta)["version"]

    @staticmethod
    def _dict_to_hba_string(_dict: dict[str, Any]) -> str:
        """Transform a dictionary into a Host Based Authentication valid string."""
        for key, value in _dict.items():
            if isinstance(value, bool):
                _dict[key] = int(value)
            if isinstance(value, str):
                _dict[key] = f'"{value}"'

        return " ".join(f"{key}={value}" for key, value in _dict.items())

    @cached_property
    def cached_cluster_status(self):
        """Cached cluster status."""
        return self.cluster_status()

    def cluster_status(self, alternative_endpoints: list | None = None) -> list[ClusterMember]:
        """Query the cluster status."""
        # Request info from cluster endpoint (which returns all members of the cluster).
        if response := self.parallel_patroni_get_request(
            f"/{PATRONI_CLUSTER_STATUS_ENDPOINT}", alternative_endpoints
        ):
            logger.debug("API cluster_status: %s", response["members"])
            return response["members"]
        raise RetryError(
            last_attempt=Future.construct(1, Exception("Unable to reach any units"), True)
        )

    async def _httpx_get_request(self, url: str, verify: bool = True) -> dict[str, Any] | None:
        if not self._patroni_async_auth:
            return None
        ssl_ctx = create_default_context()
        if verify:
            with suppress(FileNotFoundError):
                ssl_ctx.load_verify_locations(cafile=f"{self._storage_path}/{TLS_CA_FILE}")
        else:
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = CERT_NONE
        async with AsyncClient(
            auth=self._patroni_async_auth, timeout=API_REQUEST_TIMEOUT, verify=ssl_ctx
        ) as client:
            try:
                return (await client.get(url)).raise_for_status().json()
            except (HTTPError, ValueError):
                return None

    async def _async_get_request(
        self, uri: str, endpoints: list[str], verify: bool = True
    ) -> dict[str, Any] | None:
        tasks = [
            create_task(self._httpx_get_request(f"https://{ip}:8008{uri}", verify))
            for ip in endpoints
        ]
        # PG 14 still needs to check both schemas
        tasks += [
            create_task(self._httpx_get_request(f"http://{ip}:8008{uri}", verify))
            for ip in endpoints
        ]
        for task in as_completed(tasks):
            if result := await task:
                for task in tasks:
                    task.cancel()
                await wait(tasks)
                return result

    def parallel_patroni_get_request(
        self, uri: str, endpoints: list[str] | None = None
    ) -> dict[str, Any] | None:
        """Call all possible patroni endpoints in parallel."""
        if not endpoints:
            endpoints = []
            if self._endpoint:
                endpoints.append(self._endpoint)
            for endpoint in self._endpoints:
                endpoints.append(endpoint)
            verify = True
        else:
            # TODO we don't know the other cluster's ca
            verify = False
        return run(self._async_get_request(uri, endpoints, verify))

    @cached_property
    def _synchronous_node_count(self) -> int:
        planned_units = self._charm.app.planned_units()
        if self._charm.config.synchronous_node_count == "all":
            return planned_units - 1
        elif self._charm.config.synchronous_node_count == "majority":
            return planned_units // 2
        return (
            self._charm.config.synchronous_node_count
            if self._charm.config.synchronous_node_count < self._members_count - 1
            else planned_units - 1
        )

    @cached_property
    def synchronous_configuration(self) -> dict[str, Any]:
        """Synchronous mode configuration."""
        # Try to update synchronous_node_count.
        return {
            "synchronous_node_count": self._synchronous_node_count,
            "synchronous_mode_strict": self._members_count > 1
            and self._charm.config.synchronous_mode_strict
            and self._synchronous_node_count > 0,
        }

    def update_synchronous_node_count(self) -> None:
        """Update synchronous_node_count."""
        # Try to update synchronous_node_count.
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
            with attempt:
                r = requests.patch(
                    f"{self._patroni_url}/config",
                    json=self.synchronous_configuration,
                    verify=self._verify,
                    auth=self._patroni_auth,
                    timeout=PATRONI_TIMEOUT,
                )

                # Check whether the update was unsuccessful.
                if r.status_code != 200:
                    raise UpdateSyncNodeCountError(f"received {r.status_code}")

    def get_primary(
        self, unit_name_pattern=False, alternative_endpoints: list[str] | None = None
    ) -> str | None:
        """Get primary instance.

        Args:
            unit_name_pattern: whether or not to convert pod name to unit name
            alternative_endpoints: list of alternative endpoints to check for the primary.

        Returns:
            primary pod or unit name.
        """
        try:
            cluster_status = self.cluster_status(alternative_endpoints)
            for member in cluster_status:
                if member["role"] == "leader":
                    primary = member["name"]
                    if unit_name_pattern:
                        # Change the last dash to / in order to match unit name pattern.
                        primary = label2name(primary)
                    return primary
        except RetryError:
            logger.debug("Unable to get primary. Cluster status unreachable")

    def get_standby_leader(
        self, unit_name_pattern=False, check_whether_is_running: bool = False
    ) -> str | None:
        """Get standby leader instance.

        Args:
            unit_name_pattern: whether to convert pod name to unit name
            check_whether_is_running: whether to check if the standby leader is running

        Returns:
            standby leader pod or unit name.
        """
        # Request info from cluster endpoint (which returns all members of the cluster).
        cluster_status = self.cluster_status()
        if cluster_status:
            for member in cluster_status:
                if member["role"] == "standby_leader":
                    if check_whether_is_running and member["state"] not in STARTED_STATES:
                        logger.warning(f"standby leader {member['name']} is not running")
                        continue
                    standby_leader = member["name"]
                    if unit_name_pattern:
                        # Change the last dash to / in order to match unit name pattern.
                        standby_leader = label2name(standby_leader)
                    return standby_leader

    def get_sync_standby_names(self) -> list[str]:
        """Get the list of sync standby unit names."""
        sync_standbys = []
        # Request info from cluster endpoint (which returns all members of the cluster).
        cluster_status = self.cluster_status()
        if cluster_status:
            for member in cluster_status:
                if member["role"] == "sync_standby":
                    sync_standbys.append(label2name(member["name"]))
        return sync_standbys

    @cached_property
    def cluster_members(self) -> set:
        """Get the current cluster members."""
        # Request info from cluster endpoint (which returns all members of the cluster).
        return {member["name"] for member in self.cluster_status()}

    def get_running_cluster_members(self) -> list[str]:
        """List running patroni members."""
        try:
            return [
                member["name"]
                for member in self.cluster_status()
                if member["state"] in RUNNING_STATES
            ]
        except Exception:
            return []

    def are_all_members_ready(self) -> bool:
        """Check if all members are correctly running Patroni and PostgreSQL.

        Returns:
            True if all members are ready False otherwise. Retries over a period of 10 seconds
            3 times to allow server time to start up.
        """
        # Request info from cluster endpoint
        # (which returns all members of the cluster and their states).
        try:
            members = self.cluster_status()
        except RetryError:
            return False

        # Check if all members are running and one of them is a leader (primary) or
        # a standby leader, because sometimes there may exist (for some period of time)
        # only replicas after a failed switchover.
        return all(member["state"] in STARTED_STATES for member in members) and any(
            member["role"] in ["leader", "standby_leader"] for member in members
        )

    @property
    def is_creating_backup(self) -> bool:
        """Returns whether a backup is being created."""
        # Request info from cluster endpoint (which returns the list of tags from each
        # cluster member; the "is_creating_backup" tag means that the member is creating
        # a backup).
        try:
            members = self.cached_cluster_status
        except RetryError:
            return False

        return any(
            "tags" in member and member["tags"].get("is_creating_backup") for member in members
        )

    @property
    def is_replication_healthy(self) -> bool:
        """Return whether the replication is healthy."""
        try:
            for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
                with attempt:
                    primary = self.get_primary()
                    unit_id = primary.split("-")[-1]
                    primary_endpoint = (
                        f"{self._charm.app.name}-{unit_id}.{self._charm.app.name}-endpoints"
                    )
                    for member_endpoint in self._endpoints:
                        endpoint = (
                            "leader" if member_endpoint == primary_endpoint else "replica?lag=16kB"
                        )
                        url = self._patroni_url.replace(self._endpoint, member_endpoint)
                        member_status = requests.get(
                            f"{url}/{endpoint}",
                            verify=self._verify,
                            auth=self._patroni_auth,
                            timeout=PATRONI_TIMEOUT,
                        )
                        if member_status.status_code != 200:
                            raise Exception
        except RetryError:
            logger.exception("replication is not healthy")
            return False

        logger.debug("replication is healthy")
        return True

    @property
    def primary_endpoint_ready(self) -> bool:
        """Is the primary endpoint redirecting connections to the primary pod.

        Returns:
            Return whether the primary endpoint is redirecting connections to the primary pod.
        """
        try:
            for attempt in Retrying(stop=stop_after_delay(10), wait=wait_fixed(1)):
                with attempt:
                    r = requests.get(
                        f"{'https' if self._charm.is_peer_data_tls_set else 'http'}://{self._primary_endpoint}:8008/health",
                        verify=self._verify,
                        auth=self._patroni_auth,
                        timeout=PATRONI_TIMEOUT,
                    )
                    if r.json()["state"] not in RUNNING_STATES:
                        raise EndpointNotReadyError
        except RetryError:
            return False

        return True

    def get_patroni_health(self) -> dict[str, str]:
        """Gets, retires and parses the Patroni health endpoint."""
        for attempt in Retrying(stop=stop_after_delay(15), wait=wait_fixed(3)):
            with attempt:
                r = requests.get(
                    f"{self._patroni_url}/health",
                    verify=self._verify,
                    timeout=PATRONI_TIMEOUT,
                    auth=self._patroni_auth,
                )

                return r.json()

    @property
    def member_started(self) -> bool:
        """Has the member started Patroni and PostgreSQL.

        Returns:
            True if services is ready False otherwise. Retries over a period of 60 seconds times to
            allow server time to start up.
        """
        try:
            health = self.get_patroni_health()
        except RetryError:
            return False

        return health["state"] in RUNNING_STATES

    @property
    def member_streaming(self) -> bool:
        """Has the member started to stream data from primary.

        Returns:
            True if it's streaming False otherwise. Retries over a period of 60 seconds times to
            allow server time to start up.
        """
        try:
            health = self.get_patroni_health()
        except RetryError:
            return False

        return health.get("replication_state") == "streaming"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def bulk_update_parameters_controller_by_patroni(self, parameters: dict[str, Any]) -> None:
        """Update the value of a parameter controller by Patroni.

        For more information, check https://patroni.readthedocs.io/en/latest/patroni_configuration.html#postgresql-parameters-controlled-by-patroni.
        """
        requests.patch(
            f"{self._patroni_url}/config",
            verify=self._verify,
            json={
                "postgresql": {
                    "remove_data_directory_on_rewind_failure": False,
                    "remove_data_directory_on_diverged_timelines": False,
                    "parameters": parameters,
                }
            },
            auth=self._patroni_auth,
            timeout=PATRONI_TIMEOUT,
        )

    def promote_standby_cluster(self) -> None:
        """Promote a standby cluster to be a regular cluster."""
        config_response = requests.get(
            f"{self._patroni_url}/config",
            verify=self._verify,
            auth=self._patroni_auth,
            timeout=PATRONI_TIMEOUT,
        )
        if "standby_cluster" not in config_response.json():
            raise StandbyClusterAlreadyPromotedError("standby cluster is already promoted")
        requests.patch(
            f"{self._patroni_url}/config",
            verify=self._verify,
            json={"standby_cluster": None},
            auth=self._patroni_auth,
            timeout=PATRONI_TIMEOUT,
        )
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
            with attempt:
                if self.get_primary() is None:
                    raise ClusterNotPromotedError("cluster not promoted")

    def set_failsafe_mode(self) -> None:
        """Patch the DCS with failsafe mode on."""
        requests.patch(
            f"{self._patroni_url}/config",
            verify=self._verify,
            json={"failsafe_mode": True},
            auth=self._patroni_auth,
            timeout=PATRONI_TIMEOUT,
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def reinitialize_postgresql(self) -> None:
        """Reinitialize PostgreSQL."""
        requests.post(
            f"{self._patroni_url}/reinitialize",
            verify=self._verify,
            auth=self._patroni_auth,
            timeout=PATRONI_TIMEOUT,
        )

    def _render_file(self, path: str, content: str, mode: int) -> None:
        """Write a content rendered from a template to a file.

        Args:
            path: the path to the file.
            content: the data to be written to the file.
            mode: access permission mask applied to the
              file using chmod (e.g. 0o640).
        """
        with open(path, "w+") as file:
            file.write(content)
        # Ensure correct permissions are set on the file.
        os.chmod(path, mode)
        try:
            # Get the uid/gid for the postgres user.
            u = pwd.getpwnam("postgres")
            # Set the correct ownership for the file.
            os.chown(path, uid=u.pw_uid, gid=u.pw_gid)
        except KeyError:
            # Ignore non existing user error when it wasn't created yet.
            pass

    def render_patroni_yml_file(
        self,
        connectivity: bool = False,
        is_creating_backup: bool = False,
        enable_ldap: bool = False,
        enable_tls: bool = False,
        is_no_sync_member: bool = False,
        stanza: str | None = None,
        restore_stanza: str | None = None,
        disable_pgbackrest_archiving: bool = False,
        backup_id: str | None = None,
        pitr_target: str | None = None,
        restore_timeline: str | None = None,
        restore_to_latest: bool = False,
        parameters: dict[str, str] | None = None,
        user_databases_map: dict[str, str] | None = None,
    ) -> None:
        """Render the Patroni configuration file.

        Args:
            connectivity: whether to allow external connections to the database.
            enable_ldap: whether to enable LDAP authentication.
            enable_tls: whether to enable TLS.
            is_creating_backup: whether this unit is creating a backup.
            is_no_sync_member: whether this member shouldn't be a synchronous standby
                (when it's a replica).
            stanza: name of the stanza created by pgBackRest.
            restore_stanza: name of the stanza used when restoring a backup.
            disable_pgbackrest_archiving: whether to force disable pgBackRest WAL archiving.
            backup_id: id of the backup that is being restored.
            pitr_target: point-in-time-recovery target for the restore.
            restore_timeline: timeline to restore from.
            restore_to_latest: restore all the WAL transaction logs from the stanza.
            parameters: PostgreSQL parameters to be added to the postgresql.conf file.
            user_databases_map: map of databases to be accessible by each user.
        """
        # Open the template patroni.yml file.
        with open("templates/patroni.yml.j2") as file:
            template = Template(file.read())

        ldap_params = self._charm.get_ldap_parameters()

        # Render the template file with the correct values.
        rendered = template.render(
            connectivity=connectivity,
            enable_ldap=enable_ldap,
            enable_tls=enable_tls,
            endpoint=self._endpoint,
            endpoints=self._endpoints,
            is_creating_backup=is_creating_backup,
            is_no_sync_member=is_no_sync_member,
            namespace=self._namespace,
            storage_path=self._storage_path,
            superuser_password=self._superuser_password,
            replication_password=self._replication_password,
            rewind_user=REWIND_USER,
            rewind_password=self._rewind_password,
            enable_pgbackrest_archiving=stanza is not None
            and disable_pgbackrest_archiving is False,
            restoring_backup=backup_id is not None or pitr_target is not None,
            backup_id=backup_id,
            pitr_target=pitr_target if not restore_to_latest else None,
            restore_timeline=restore_timeline,
            restore_to_latest=restore_to_latest,
            stanza=stanza,
            restore_stanza=restore_stanza,
            synchronous_node_count=self._synchronous_node_count,
            maximum_lag_on_failover=self._charm.config.durability_maximum_lag_on_failover,
            version=self.rock_postgresql_version.split(".")[0],
            pg_parameters=parameters,
            primary_cluster_endpoint=self._charm.async_replication.get_primary_cluster_endpoint(),
            extra_replication_endpoints=self._charm.async_replication.get_standby_endpoints(),
            ldap_parameters=self._dict_to_hba_string(ldap_params),
            patroni_password=self._patroni_password,
            user_databases_map=user_databases_map,
        )
        self._render_file(f"{self._storage_path}/patroni.yml", rendered, 0o644)

    def reload_patroni_configuration(self) -> None:
        """Reloads the configuration after it was updated in the file."""
        container = self._charm.unit.get_container("postgresql")
        if container.can_connect():
            services = container.pebble.get_services(names=[self._charm.postgresql_service])
            if len(services) > 0 and services[0].is_running():
                container.send_signal(SIGHUP, self._charm.postgresql_service)
                return
        logger.warning("Unable to find Patroni service. Skipping reload")

    def last_postgresql_logs(self) -> str:
        """Get last log file content of Postgresql service in the container.

        If there is no available log files, empty line will be returned.

        Returns:
            Content of last log file of Postgresql service.
        """
        container = self._charm.unit.get_container("postgresql")
        if not container.can_connect():
            logger.debug("Cannot get last PostgreSQL log from Rock. Container inaccessible")
            return ""
        try:
            log_files = container.list_files(POSTGRESQL_LOGS_PATH, pattern=POSTGRESQL_LOGS_PATTERN)
            if len(log_files) == 0:
                return ""
            log_files.sort(key=lambda f: f.path, reverse=True)
            with container.pull(log_files[0].path) as last_log_file:
                return last_log_file.read()
        except Error:
            error_message = "Failed to read last postgresql log file"
            logger.exception(error_message)
            return ""

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def restart_postgresql(self) -> None:
        """Restart PostgreSQL."""
        requests.post(
            f"{self._patroni_url}/restart",
            verify=self._verify,
            auth=self._patroni_auth,
            timeout=PATRONI_TIMEOUT,
        )

    def switchover(self, candidate: str | None = None, wait: bool = True) -> None:
        """Trigger a switchover."""
        # Try to trigger the switchover.
        if candidate is not None:
            candidate = candidate.replace("/", "-")

        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
            with attempt:
                primary = self.get_primary()
                r = requests.post(
                    f"{self._patroni_url}/switchover",
                    json={"leader": primary, "candidate": candidate},
                    verify=self._verify,
                    auth=self._patroni_auth,
                    timeout=PATRONI_TIMEOUT,
                )

        # Check whether the switchover was unsuccessful.
        if r.status_code != 200:
            if (
                r.status_code == 412
                and r.text == "candidate name does not match with sync_standby"
            ):
                logger.debug("Unit is not sync standby")
                raise SwitchoverNotSyncError()
            logger.warning(f"Switchover call failed with code {r.status_code} {r.text}")
            raise SwitchoverFailedError(f"received {r.status_code}")

        if not wait:
            return

        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3), reraise=True):
            with attempt:
                new_primary = self.get_primary()
                if (candidate is not None and new_primary != candidate) or new_primary == primary:
                    raise SwitchoverFailedError("primary was not switched correctly")

    @retry(
        retry=retry_if_result(lambda x: not x),
        stop=stop_after_attempt(10),
        wait=wait_exponential(multiplier=1, min=2, max=30),
    )
    def primary_changed(self, old_primary: str) -> bool:
        """Checks whether the primary unit has changed."""
        primary = self.get_primary()
        return primary != old_primary
