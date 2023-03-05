#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helper class used to manage interactions with Patroni API and configuration files."""

import logging
import os
import pwd
from typing import List, Optional

import requests
from jinja2 import Template
from tenacity import (
    AttemptManager,
    RetryError,
    Retrying,
    retry,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential,
    wait_fixed,
)

from constants import REWIND_USER, TLS_CA_FILE

logger = logging.getLogger(__name__)


class NotReadyError(Exception):
    """Raised when not all cluster members healthy or finished initial sync."""


class EndpointNotReadyError(Exception):
    """Raised when an endpoint is not ready."""


class Patroni:
    """This class handles the communication with Patroni API and configuration files."""

    def __init__(
        self,
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

    @property
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def cluster_members(self) -> set:
        """Get the current cluster members."""
        # Request info from cluster endpoint (which returns all members of the cluster).
        r = requests.get(f"{self._patroni_url}/cluster", verify=self._verify)
        return set([member["name"] for member in r.json()["members"]])

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

        return all(member["state"] == "running" for member in r.json()["members"])

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
                    if r.json()["state"] != "running":
                        raise EndpointNotReadyError
        except RetryError:
            return False

        return True

    @property
    def member_started(self) -> bool:
        """Has the member started Patroni and PostgreSQL.

        Returns:
            True if services is ready False otherwise. Retries over a period of 60 seconds times to
            allow server time to start up.
        """
        try:
            for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
                with attempt:
                    r = requests.get(f"{self._patroni_url}/health", verify=self._verify)
        except RetryError:
            return False

        return r.json()["state"] == "running"

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
        archive_mode: bool,
        enable_tls: bool = False,
        stanza: str = None,
        backup_id: Optional[str] = None,
    ) -> None:
        """Render the Patroni configuration file.

        Args:
            archive_mode: PostgreSQL archive mode.
            enable_tls: whether to enable TLS.
            stanza: name of the stanza created by pgBackRest.
            backup_id: id of the backup that is being restored.
        """
        # Open the template postgresql.conf file.
        with open("templates/patroni.yml.j2", "r") as file:
            template = Template(file.read())
        # Render the template file with the correct values.
        rendered = template.render(
            archive_mode=archive_mode,
            enable_tls=enable_tls,
            endpoint=self._endpoint,
            endpoints=self._endpoints,
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
        )
        self._render_file(f"{self._storage_path}/patroni.yml", rendered, 0o644)

    def render_postgresql_conf_file(self) -> None:
        """Render the PostgreSQL configuration file."""
        # Open the template postgresql.conf file.
        with open("templates/postgresql.conf.j2", "r") as file:
            template = Template(file.read())
        # Render the template file with the correct values.
        # TODO: add extra configurations here later.
        rendered = template.render(
            logging_collector="on",
            synchronous_commit="on" if self._members_count > 1 else "off",
            synchronous_standby_names="*",
        )
        self._render_file(f"{self._storage_path}/postgresql-k8s-operator.conf", rendered, 0o644)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def reload_patroni_configuration(self) -> None:
        """Reloads the configuration after it was updated in the file."""
        requests.post(f"{self._patroni_url}/reload", verify=self._verify)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def restart_postgresql(self) -> None:
        """Restart PostgreSQL."""
        requests.post(f"{self._patroni_url}/restart", verify=self._verify)
