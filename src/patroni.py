#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helper class used to manage interactions with Patroni API and configuration files."""

import logging
import os
import pwd
from typing import List

import requests
from jinja2 import Template
from tenacity import RetryError, retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class NotReadyError(Exception):
    """Raised when not all cluster members healthy or finished initial sync."""


class Patroni:
    """This class handles the communication with Patroni API and configuration files."""

    def __init__(
        self, endpoint: str, endpoints: List[str], namespace: str, pod_ip: str, storage_path: str
    ):
        self._endpoint = endpoint
        self._endpoints = endpoints
        self._namespace = namespace
        self._pod_ip = pod_ip
        self._storage_path = storage_path

    def change_master_start_timeout(self, seconds: int) -> None:
        """Change master start timeout configuration.

        Args:
            seconds: number of seconds to set in master_start_timeout configuration.
        """
        requests.patch(
            f"http://{self._endpoint}:8008/config",
            json={"master_start_timeout": seconds},
        )

    def get_primary(self, unit_name_pattern=False) -> str:
        """Get primary instance.

        Args:
            unit_name_pattern: whether or not to convert pod name to unit name

        Returns:
            primary pod or unit name.
        """
        primary = None
        # Request info from cluster endpoint (which returns all members of the cluster).
        r = requests.get(f"http://{self._endpoint}:8008/cluster")
        for member in r.json()["members"]:
            if member["role"] == "leader":
                primary = member["name"]
                if unit_name_pattern:
                    # Change the last dash to / in order to match unit name pattern.
                    primary = "/".join(primary.rsplit("-", 1))
                break
        return primary

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def get_postgresql_state(self) -> str:
        """Get PostgreSQL state.

        Returns:
            running, restarting or stopping.
        """
        r = requests.get(f"http://{self._endpoint}:8008/health")
        return r.json()["state"]

    @property
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def cluster_members(self) -> set:
        """Get the current cluster members."""
        # Request info from cluster endpoint (which returns all members of the cluster).
        r = requests.get(f"http://{self._endpoint}:8008/cluster")
        return set([member["name"] for member in r.json()["members"]])

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def are_all_members_ready(self) -> bool:
        """Check if all members are correctly running Patroni and PostgreSQL."""
        # Request info from cluster endpoint
        # (which returns all members of the cluster and their states).
        r = requests.get(f"http://{self._endpoint}:8008/cluster")
        return all(member["state"] == "running" for member in r.json()["members"])

    @property
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def member_started(self) -> bool:
        """Returns whether the member started Patroni and PostgreSQL."""
        r = requests.get(f"http://{self._endpoint}:8008/health")
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

    def render_patroni_yml_file(self) -> None:
        """Render the Patroni configuration file."""
        # Open the template postgresql.conf file.
        with open("templates/patroni.yml.j2", "r") as file:
            template = Template(file.read())
        # Render the template file with the correct values.
        rendered = template.render(
            endpoint=self._endpoint,
            endpoints=self._endpoints,
            namespace=self._namespace,
            pod_ip=self._pod_ip,
            storage_path=self._storage_path,
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
            logging_collector="on", synchronous_commit="on", synchronous_standby_names="*"
        )
        self._render_file(f"{self._storage_path}/postgresql-k8s-operator.conf", rendered, 0o644)

    def update_cluster_members(self) -> None:
        """Update the list of members of the cluster."""
        # Update the members in the Patroni configuration.
        self.render_patroni_yml_file()

        try:
            if self.member_started:
                # Make Patroni use the updated configuration.
                self._reload_patroni_configuration()
        except RetryError:
            # Ignore retry errors that happen when the member has not started yet.
            # The configuration will be loaded correctly when Patroni starts.
            pass

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _reload_patroni_configuration(self):
        """Reloads the configuration after it was updated in the file."""
        requests.post(f"http://{self._endpoint}:8008/reload")
