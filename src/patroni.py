#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helper class used to manage interactions with Patroni API and configuration files."""

import logging
import os
import pwd
from typing import Any, TypedDict

import requests
import yaml
from jinja2 import Template
from ops.pebble import Error
from requests.auth import HTTPBasicAuth
from tenacity import (
    AttemptManager,
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
    POSTGRESQL_LOGS_PATH,
    POSTGRESQL_LOGS_PATTERN,
    REWIND_USER,
    TLS_CA_BUNDLE_FILE,
)

RUNNING_STATES = ["running", "streaming"]
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
        superuser_password: str | None,
        replication_password: str | None,
        rewind_password: str | None,
        patroni_password: str | None,
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
        # Variable mapping to requests library verify parameter.
        # The CA bundle file is used to validate the server certificate when
        # TLS is enabled, otherwise True is set because it's the default value.
        self._verify = f"{self._storage_path}/{TLS_CA_BUNDLE_FILE}"

    @property
    def _patroni_auth(self) -> HTTPBasicAuth | None:
        if self._patroni_password:
            return HTTPBasicAuth("patroni", self._patroni_password)

    @property
    def _patroni_url(self) -> str:
        """Patroni REST API URL."""
        return f"https://{self._endpoint}:8008"

    @property
    def rock_postgresql_version(self) -> str:
        """Version of Postgresql installed in the Rock image."""
        container = self._charm.unit.get_container("postgresql")
        if not container.can_connect():
            logger.debug("Cannot get Postgresql version from Rock. Container inaccessible")
            # TODO replace with refresh v3 manifest
            return ""
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

    def _get_alternative_patroni_url(
        self, attempt: AttemptManager, alternative_endpoints: list[str] | None = None
    ) -> str:
        """Get an alternative REST API URL from another member each time.

        When the Patroni process is not running in the current unit it's needed
        to use a URL from another cluster member REST API to do some operations.
        """
        if alternative_endpoints is not None:
            return self._patroni_url.replace(
                self._endpoint, alternative_endpoints[attempt.retry_state.attempt_number - 1]
            )
        if attempt.retry_state.attempt_number > 1:
            url = self._patroni_url.replace(
                self._endpoint, list(self._endpoints)[attempt.retry_state.attempt_number - 2]
            )
        else:
            url = self._patroni_url
        return url

    @property
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

    def update_synchronous_node_count(self) -> None:
        """Update synchronous_node_count."""
        # Try to update synchronous_node_count.
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
            with attempt:
                r = requests.patch(
                    f"{self._patroni_url}/config",
                    json={"synchronous_node_count": self._synchronous_node_count},
                    verify=self._verify,
                    auth=self._patroni_auth,
                    timeout=PATRONI_TIMEOUT,
                )

                # Check whether the update was unsuccessful.
                if r.status_code != 200:
                    raise UpdateSyncNodeCountError(f"received {r.status_code}")

    def get_cluster(
        self, attempt: AttemptManager, alternative_endpoints: list[str] | None = None
    ) -> dict[str, Any]:
        """Call the cluster endpoint."""
        url = self._get_alternative_patroni_url(attempt, alternative_endpoints)
        r = requests.get(
            f"{url}/cluster",
            verify=self._verify,
            auth=self._patroni_auth,
            timeout=PATRONI_TIMEOUT,
        )
        return r.json()

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
        primary = None
        # Request info from cluster endpoint (which returns all members of the cluster).
        for attempt in Retrying(stop=stop_after_attempt(len(self._endpoints) + 1)):
            with attempt:
                for member in self.get_cluster(attempt, alternative_endpoints)["members"]:
                    if member["role"] == "leader":
                        primary = member["name"]
                        if unit_name_pattern:
                            # Change the last dash to / in order to match unit name pattern.
                            primary = "/".join(primary.rsplit("-", 1))
                        break
        return primary

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
        standby_leader = None
        # Request info from cluster endpoint (which returns all members of the cluster).
        for attempt in Retrying(stop=stop_after_attempt(len(self._endpoints) + 1)):
            with attempt:
                for member in self.get_cluster(attempt)["members"]:
                    if member["role"] == "standby_leader":
                        if check_whether_is_running and member["state"] not in RUNNING_STATES:
                            logger.warning(f"standby leader {member['name']} is not running")
                            continue
                        standby_leader = member["name"]
                        if unit_name_pattern:
                            # Change the last dash to / in order to match unit name pattern.
                            standby_leader = "/".join(standby_leader.rsplit("-", 1))
                        break
        return standby_leader

    def get_sync_standby_names(self) -> list[str]:
        """Get the list of sync standby unit names."""
        sync_standbys = []
        # Request info from cluster endpoint (which returns all members of the cluster).
        for attempt in Retrying(stop=stop_after_attempt(len(self._endpoints) + 1)):
            with attempt:
                for member in self.get_cluster(attempt)["members"]:
                    if member["role"] == "sync_standby":
                        sync_standbys.append("/".join(member["name"].rsplit("-", 1)))
        return sync_standbys

    @property
    def cluster_members(self) -> set:
        """Get the current cluster members."""
        # Request info from cluster endpoint (which returns all members of the cluster).
        for attempt in Retrying(
            stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10)
        ):
            with attempt:
                return {member["name"] for member in self.get_cluster(attempt)["members"]}
        return set()

    def get_running_cluster_members(self) -> list[str]:
        """List running patroni members."""
        try:
            for attempt in Retrying(stop=stop_after_attempt(1)):
                with attempt:
                    return [
                        member["name"]
                        for member in self.get_cluster(attempt)["members"]
                        if member["state"] in RUNNING_STATES
                    ]
        except Exception:
            return []
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
            for attempt in Retrying(stop=stop_after_delay(10), wait=wait_fixed(3)):
                with attempt:
                    return all(
                        member["state"] in RUNNING_STATES
                        for member in self.get_cluster(attempt)["members"]
                    )
        except RetryError:
            return False
        return False

    @property
    def is_creating_backup(self) -> bool:
        """Returns whether a backup is being created."""
        # Request info from cluster endpoint (which returns the list of tags from each
        # cluster member; the "is_creating_backup" tag means that the member is creating
        # a backup).
        try:
            for attempt in Retrying(stop=stop_after_delay(10), wait=wait_fixed(3)):
                with attempt:
                    return any(
                        "tags" in member and member["tags"].get("is_creating_backup")
                        for member in self.get_cluster(attempt)["members"]
                    )
        except RetryError:
            return False
        return False

    @property
    def is_replication_healthy(self) -> bool:
        """Return whether the replication is healthy."""
        try:
            for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
                with attempt:
                    if not (primary := self.get_primary()):
                        logger.debug("Failed replication check no primary reported")
                        raise Exception

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
                        f"https://{self._primary_endpoint}:8008/health",
                        verify=self._verify,
                        auth=self._patroni_auth,
                        timeout=PATRONI_TIMEOUT,
                    )
                    if r.json()["state"] not in RUNNING_STATES:
                        raise EndpointNotReadyError
        except RetryError:
            return False

        return True

    @property
    def member_replication_lag(self) -> str:
        """Member replication lag."""
        try:
            for attempt in Retrying(stop=stop_after_delay(10), wait=wait_fixed(1)):
                with attempt:
                    cluster_status = requests.get(
                        f"{self._patroni_url}/cluster",
                        verify=self._verify,
                        timeout=5,
                        auth=self._patroni_auth,
                    )
        except RetryError:
            return "unknown"

        for member in cluster_status.json()["members"]:
            if member["name"] == self._charm.unit.name.replace("/", "-"):
                return member.get("lag", "unknown")

        return "unknown"

    @property
    def member_started(self) -> bool:
        """Has the member started Patroni and PostgreSQL.

        Returns:
            True if services is ready False otherwise. Retries over a period of 60 seconds times to
            allow server time to start up.
        """
        try:
            for attempt in Retrying(stop=stop_after_delay(10), wait=wait_fixed(1)):
                with attempt:
                    r = requests.get(
                        f"{self._patroni_url}/health",
                        verify=self._verify,
                        auth=self._patroni_auth,
                        timeout=PATRONI_TIMEOUT,
                    )
        except RetryError:
            return False

        return r.json()["state"] in RUNNING_STATES

    @property
    def member_streaming(self) -> bool:
        """Has the member started to stream data from primary.

        Returns:
            True if it's streaming False otherwise. Retries over a period of 60 seconds times to
            allow server time to start up.
        """
        try:
            for attempt in Retrying(stop=stop_after_delay(10), wait=wait_fixed(1)):
                with attempt:
                    r = requests.get(
                        f"{self._patroni_url}/health",
                        verify=self._verify,
                        auth=self._patroni_auth,
                        timeout=PATRONI_TIMEOUT,
                    )
        except RetryError:
            return False

        return r.json().get("replication_state") == "streaming"

    @property
    def is_database_running(self) -> bool:
        """Returns whether the PostgreSQL database process is running (and isn't frozen)."""
        container = self._charm.unit.get_container("postgresql")
        output = container.exec(["ps", "aux"]).wait_output()
        postgresql_processes = [
            process
            for process in output[0].split("/n")
            if "/usr/lib/postgresql/14/bin/postgres" in process
        ]
        # Check whether the PostgreSQL process has a state equal to T (frozen).
        return any(process for process in postgresql_processes if process.split()[7] != "T")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def bulk_update_parameters_controller_by_patroni(self, parameters: dict[str, Any]) -> None:
        """Update the value of a parameter controller by Patroni.

        For more information, check https://patroni.readthedocs.io/en/latest/patroni_configuration.html#postgresql-parameters-controlled-by-patroni.
        """
        requests.patch(
            f"{self._patroni_url}/config",
            verify=self._verify,
            json={"postgresql": {"parameters": parameters}},
            auth=self._patroni_auth,
            timeout=PATRONI_TIMEOUT,
        )

    def ensure_slots_controller_by_patroni(self, slots: dict[str, str]) -> None:
        """Synchronises slots controlled by Patroni with the provided state by removing unneeded slots and creating new ones.

        Args:
            slots: dictionary of slots in the {slot: database} format.
        """
        current_config = requests.get(
            f"{self._patroni_url}/config",
            verify=self._verify,
            timeout=PATRONI_TIMEOUT,
            auth=self._patroni_auth,
        )
        slots_patch: dict[str, dict[str, str] | None] = dict.fromkeys(
            current_config.json().get("slots", ())
        )
        for slot, database in slots.items():
            slots_patch[slot] = {
                "database": database,
                "plugin": "pgoutput",
                "type": "logical",
            }
        requests.patch(
            f"{self._patroni_url}/config",
            verify=self._verify,
            json={"slots": slots_patch},
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
        slots: dict[str, str] | None = None,
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
            slots: replication slots (keys) with assigned database name (values).
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
            version=self.rock_postgresql_version.split(".")[0],
            pg_parameters=parameters,
            primary_cluster_endpoint=self._charm.async_replication.get_primary_cluster_endpoint(),
            extra_replication_endpoints=self._charm.async_replication.get_standby_endpoints(),
            ldap_parameters=self._dict_to_hba_string(ldap_params),
            patroni_password=self._patroni_password,
            user_databases_map=user_databases_map,
            slots=slots,
            instance_password_encryption=self._charm.config.instance_password_encryption,
        )
        self._render_file(f"{self._storage_path}/patroni.yml", rendered, 0o644)

    @retry(stop=stop_after_attempt(20), wait=wait_exponential(multiplier=1, min=2, max=30))
    def reload_patroni_configuration(self) -> None:
        """Reloads the configuration after it was updated in the file."""
        requests.post(
            f"{self._patroni_url}/reload",
            verify=self._verify,
            auth=self._patroni_auth,
            timeout=PATRONI_TIMEOUT,
        )

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
