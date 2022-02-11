#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helper class used to manage interactions with Patroni API and configuration files."""

import logging
import os
import pwd

import requests
from jinja2 import Template
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


STORAGE_PATH = "/var/lib/postgresql/data"


class Patroni:
    """This class handles the communication with Patroni API and configuration files."""

    def __init__(self, pod_ip: str):
        self._pod_ip = pod_ip

    def get_primary(self, unit_name_pattern=False) -> str:
        """Get primary instance.

        Args:
            unit_name_pattern: whether or not to convert pod name to unit name

        Returns:
            primary pod or unit name.
        """
        primary = None
        # Request info from cluster endpoint (which returns all members of the cluster).
        r = requests.get(f"http://{self._pod_ip}:8008/cluster")
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
        r = requests.get(f"http://{self._pod_ip}:8008/health")
        return r.json()["state"]

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
        rendered = template.render(pod_ip=self._pod_ip)
        self._render_file(f"{STORAGE_PATH}/patroni.yml", rendered, 0o644)

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
        self._render_file(f"{STORAGE_PATH}/postgresql-k8s-operator.conf", rendered, 0o644)
