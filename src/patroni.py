#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helper class used to manage interactions with Patroni API and configuration files."""

import logging
import os
import pwd
from typing import Any, Dict, List, Optional

import requests
import yaml
from jinja2 import Template
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

from constants import REWIND_USER, TLS_CA_FILE

RUNNING_STATES = ["running", "streaming"]

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


class Patroni:
    """This class handles the communication with Patroni API and configuration files."""

    def __init__(
        self,
        charm,
        endpoint: str,
        endpoints: List[str],
        primary_endpoint: str,
        namespace: str,
        storage_path: str,
        superuser_password: str,
        replication_password: str,
        rewind_password: str,
        tls_enabled: bool,
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
        self._tls_enabled = tls_enabled
        # Variable mapping to requests library verify parameter.
        # The CA bundle file is used to validate the server certificate when
        # TLS is enabled, otherwise True is set because it's the default value.
        self._verify = f"{self._storage_path}/{TLS_CA_FILE}" if tls_enabled else True

    @property
    def _patroni_url(self) -> str:
        """Patroni REST API URL."""
        return f"{'https' if self._tls_enabled else 'http'}://{self._endpoint}:8008"

    # def configure_standby_cluster(self, host: str) -> None:
    #     """Configure this cluster as a standby cluster."""
    #     requests.patch(
    #         f"{self._patroni_url}/config",
    #         verify=self._verify,
    #         json={
    #             "standby_cluster": {
    #                 "create_replica_methods": ["basebackup"],
    #                 "host": host,
    #                 "port": 5432,
    #             }
    #         },
    #     )

    @property
    def rock_postgresql_version(self) -> Optional[str]:
        """Version of Postgresql installed in the Rock image."""
        container = self._charm.unit.get_container("postgresql")
        if not container.can_connect():
            logger.debug("Cannot get Postgresql version from Rock. Container inaccessible")
            return
        snap_meta = container.pull("/meta.charmed-postgresql/snap.yaml")
        return yaml.safe_load(snap_meta)["version"]

    def _get_alternative_patroni_url(self, attempt: AttemptManager) -> str:
        """Get an alternative REST API URL from another member each time.

        When the Patroni process is not running in the current unit it's needed
        to use a URL from another cluster member REST API to do some operations.
        """
        if attempt.retry_state.attempt_number > 1:
            url = self._patroni_url.replace(
                self._endpoint, list(self._endpoints)[attempt.retry_state.attempt_number - 2]
            )
        else:
            url = self._patroni_url
        return url

    def get_primary(self, unit_name_pattern=False) -> str:
        """Get primary instance.

        Args:
            unit_name_pattern: whether or not to convert pod name to unit name

        Returns:
            primary pod or unit name.
        """
        primary = None
        # Request info from cluster endpoint (which returns all members of the cluster).
        for attempt in Retrying(stop=stop_after_attempt(len(self._endpoints) + 1)):
            with attempt:
                url = self._get_alternative_patroni_url(attempt)
                r = requests.get(f"{url}/cluster", verify=self._verify)
                for member in r.json()["members"]:
                    if member["role"] == "leader":
                        primary = member["name"]
                        if unit_name_pattern:
                            # Change the last dash to / in order to match unit name pattern.
                            primary = "/".join(primary.rsplit("-", 1))
                        break
        return primary

    def get_standby_leader(
        self, unit_name_pattern=False, check_whether_is_running: bool = False
    ) -> Optional[str]:
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
                url = self._get_alternative_patroni_url(attempt)
                r = requests.get(f"{url}/cluster", verify=self._verify)
                for member in r.json()["members"]:
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

    def get_sync_standby_names(self) -> List[str]:
        """Get the list of sync standby unit names."""
        sync_standbys = []
        # Request info from cluster endpoint (which returns all members of the cluster).
        for attempt in Retrying(stop=stop_after_attempt(len(self._endpoints) + 1)):
            with attempt:
                url = self._get_alternative_patroni_url(attempt)
                r = requests.get(f"{url}/cluster", verify=self._verify)
                for member in r.json()["members"]:
                    if member["role"] == "sync_standby":
                        sync_standbys.append("/".join(member["name"].rsplit("-", 1)))
        return sync_standbys

    @property
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def cluster_members(self) -> set:
        """Get the current cluster members."""
        # Request info from cluster endpoint (which returns all members of the cluster).
        r = requests.get(f"{self._patroni_url}/cluster", verify=self._verify)
        return {member["name"] for member in r.json()["members"]}

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
                    r = requests.get(f"{self._patroni_url}/cluster", verify=self._verify)
        except RetryError:
            return False

        return all(member["state"] in RUNNING_STATES for member in r.json()["members"])

    @property
    def is_creating_backup(self) -> bool:
        """Returns whether a backup is being created."""
        # Request info from cluster endpoint (which returns the list of tags from each
        # cluster member; the "is_creating_backup" tag means that the member is creating
        # a backup).
        try:
            for attempt in Retrying(stop=stop_after_delay(10), wait=wait_fixed(3)):
                with attempt:
                    r = requests.get(f"{self._patroni_url}/cluster", verify=self._verify)
        except RetryError:
            return False

        return any(
            "tags" in member and member["tags"].get("is_creating_backup")
            for member in r.json()["members"]
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
                        member_status = requests.get(f"{url}/{endpoint}", verify=self._verify)
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
            for attempt in Retrying(stop=stop_after_delay(10), wait=wait_fixed(3)):
                with attempt:
                    r = requests.get(
                        f"{'https' if self._tls_enabled else 'http'}://{self._primary_endpoint}:8008/health",
                        verify=self._verify,
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
            for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
                with attempt:
                    cluster_status = requests.get(
                        f"{self._patroni_url}/cluster",
                        verify=self._verify,
                        timeout=5,
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
            for attempt in Retrying(stop=stop_after_delay(90), wait=wait_fixed(3)):
                with attempt:
                    r = requests.get(f"{self._patroni_url}/health", verify=self._verify)
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
            for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
                with attempt:
                    r = requests.get(f"{self._patroni_url}/health", verify=self._verify)
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
    def bulk_update_parameters_controller_by_patroni(self, parameters: Dict[str, Any]) -> None:
        """Update the value of a parameter controller by Patroni.

        For more information, check https://patroni.readthedocs.io/en/latest/patroni_configuration.html#postgresql-parameters-controlled-by-patroni.
        """
        requests.patch(
            f"{self._patroni_url}/config",
            verify=self._verify,
            json={"postgresql": {"parameters": parameters}},
        )

    def promote_standby_cluster(self) -> None:
        """Promote a standby cluster to be a regular cluster."""
        config_response = requests.get(f"{self._patroni_url}/config", verify=self._verify)
        if "standby_cluster" not in config_response.json():
            raise StandbyClusterAlreadyPromotedError("standby cluster is already promoted")
        requests.patch(
            f"{self._patroni_url}/config", verify=self._verify, json={"standby_cluster": None}
        )
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
            with attempt:
                if self.get_primary() is None:
                    raise ClusterNotPromotedError("cluster not promoted")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def reinitialize_postgresql(self) -> None:
        """Reinitialize PostgreSQL."""
        requests.post(f"{self._patroni_url}/reinitialize", verify=self._verify)

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
        enable_tls: bool = False,
        is_no_sync_member: bool = False,
        stanza: str = None,
        restore_stanza: Optional[str] = None,
        backup_id: Optional[str] = None,
        parameters: Optional[dict[str, str]] = None,
    ) -> None:
        """Render the Patroni configuration file.

        Args:
            connectivity: whether to allow external connections to the database.
            enable_tls: whether to enable TLS.
            is_creating_backup: whether this unit is creating a backup.
            is_no_sync_member: whether this member shouldn't be a synchronous standby
                (when it's a replica).
            stanza: name of the stanza created by pgBackRest.
            restore_stanza: name of the stanza used when restoring a backup.
            backup_id: id of the backup that is being restored.
            parameters: PostgreSQL parameters to be added to the postgresql.conf file.
        """
        # Open the template patroni.yml file.
        with open("templates/patroni.yml.j2", "r") as file:
            template = Template(file.read())
        # Render the template file with the correct values.
        rendered = template.render(
            connectivity=connectivity,
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
            enable_pgbackrest=stanza is not None,
            restoring_backup=backup_id is not None,
            backup_id=backup_id,
            stanza=stanza,
            restore_stanza=restore_stanza,
            minority_count=self._members_count // 2,
            version=self.rock_postgresql_version.split(".")[0],
            pg_parameters=parameters,
            primary_cluster_endpoint=self._charm.async_replication.get_primary_cluster_endpoint(),
            extra_replication_endpoints=self._charm.async_replication.get_standby_endpoints(),
        )
        self._render_file(f"{self._storage_path}/patroni.yml", rendered, 0o644)

    @retry(stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30))
    def reload_patroni_configuration(self) -> None:
        """Reloads the configuration after it was updated in the file."""
        requests.post(f"{self._patroni_url}/reload", verify=self._verify)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def restart_postgresql(self) -> None:
        """Restart PostgreSQL."""
        requests.post(f"{self._patroni_url}/restart", verify=self._verify)

    def switchover(self, candidate: str = None) -> None:
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
                )

        # Check whether the switchover was unsuccessful.
        if r.status_code != 200:
            raise SwitchoverFailedError(f"received {r.status_code}")

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
