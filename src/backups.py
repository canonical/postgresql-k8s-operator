# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Backups implementation."""

import logging
import re
from datetime import datetime

from charms.data_platform_libs.v0.s3 import CredentialsChangedEvent, S3Requirer
from jinja2 import Template
from ops import pebble
from ops.framework import Object
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus
from tenacity import RetryError, Retrying, stop_after_attempt, wait_fixed

from constants import BACKUP_USER, WORKLOAD_OS_GROUP, WORKLOAD_OS_USER

logger = logging.getLogger(__name__)


class PostgreSQLBackups(Object):
    """In this class, we manage PostgreSQL backups."""

    def __init__(self, charm, relation_name: str):
        """Manager of PostgreSQL backups."""
        super().__init__(charm, "backup")
        self.charm = charm
        self.relation_name = relation_name

        # s3 relation handles the config options for s3 backups
        self.s3_client = S3Requirer(self.charm, self.relation_name)
        self.framework.observe(
            self.s3_client.on.credentials_changed, self._on_s3_credential_changed
        )
        self.framework.observe(self.charm.on.create_backup_action, self._on_create_backup_action)
        self.framework.observe(self.charm.on.list_backups_action, self._on_list_backups_action)

    def _on_s3_credential_changed(self, event: CredentialsChangedEvent):
        """TODO."""
        logger.error("called S3 credentials changed 1")
        if "cluster_initialised" not in self.charm.app_peer_data:
            logger.debug("Cannot set pgBackRest configurations, PostgreSQL has not yet started.")
            event.defer()
            return

        logger.error("called S3 credentials changed 2")
        self.initialise_stanza()
        logger.error("called S3 credentials changed 3")

    def _on_create_backup_action(self, event) -> None:
        if self.model.get_relation(self.relation_name) is None:
            event.fail("Relation with s3-integrator charm missing, cannot create backup.")
            return

        try:
            container = self.charm.unit.get_container("postgresql")
            process = container.exec(
                [
                    "pgbackrest",
                    "--stanza=main",
                    "--type=full",
                    "backup",
                ],
                user=WORKLOAD_OS_USER,
                group=WORKLOAD_OS_GROUP,
            )
            output, other = process.wait_output()
            logger.info(f"output 2: {output}")
            logger.info(f"other 2: {other}")
            event.set_results({"backup-status": "backup started"})
            self.charm.unit.status = MaintenanceStatus("backup started/running")
        except pebble.ExecError as e:
            event.fail(f"Failed to backup PostgreSQL with error: {str(e)}")

    def _on_list_backups_action(self, event) -> None:
        # if self.model.get_relation(self.relation_name) is None:
        #     event.fail("Relation with s3-integrator charm missing, cannot create backup.")
        #     return

        # cannot list backups if pbm is resyncing, or has incompatible options or incorrect
        # credentials
        # pbm_status = self._get_pbm_status()
        # self.charm.unit.status = pbm_status
        # if isinstance(pbm_status, WaitingStatus):
        #     event.defer()
        #     logger.debug(
        #         "Sync-ing configurations needs more time, must wait before listing backups."
        #     )
        #     return
        # if isinstance(pbm_status, BlockedStatus):
        #     event.fail(f"Cannot list backups: {pbm_status.message}.")
        #     return

        try:
            container = self.charm.unit.get_container("postgresql")
            process = container.exec(
                [
                    "pgbackrest",
                    "repo-ls",
                    "backup/main",
                    # '--filter="(F|D|I)$"',
                ],
                user=WORKLOAD_OS_USER,
                group=WORKLOAD_OS_GROUP,
            )
            command = " ".join(
                [
                    "pgbackrest",
                    "repo-ls",
                    "backup/main",
                    # "--filter='(F|D|I)$'",
                ]
            )
            logger.error(f"command: {command}")
            output, other = process.wait_output()
            logger.info(f"output list: {output}")
            logger.info(f"other list: {other}")
            backup_ids = re.findall(r".*[F]$", output, re.MULTILINE)
            backup_ids = [
                datetime.strftime(
                    datetime.strptime(backup_id[:-1], "%Y%m%d-%H%M%S"), "%Y-%m-%dT%H:%M:%SZ"
                )
                for backup_id in backup_ids
            ]
            logger.info(f"backup_ids: {backup_ids}")
            event.set_results({"backup-list": backup_ids})
        except pebble.ExecError as e:
            event.fail(f"Failed to list PostgreSQL backups with error: {str(e)}")
            return

    def initialise_stanza(self) -> bool:
        """Initialize the stanza."""
        if self.model.get_relation(self.relation_name) is None:
            return True

        try:
            self.charm.unit.status = MaintenanceStatus("creating stanza")
            credentials = self.s3_client.get_s3_connection_info()
            self._render_pgbackrest_conf_file(credentials)
            container = self.charm.unit.get_container("postgresql")
            process = container.exec(
                [
                    "pgbackrest",
                    "--stanza=main",
                    "stanza-create",
                ],
                user=WORKLOAD_OS_USER,
                group=WORKLOAD_OS_GROUP,
            )
            output, other = process.wait_output()
            logger.info(f"output: {output}")
            logger.info(f"other: {other}")
            self.charm._peers.data[self.charm.unit].update({"stanza": "main"})
            self.charm.update_config()
            logger.error(f"member started: {self.charm._patroni.member_started}")
            process = container.exec(
                [
                    "cat",
                    f"{self.charm._storage_path}/patroni.yml",
                ],
                user=WORKLOAD_OS_USER,
                group=WORKLOAD_OS_GROUP,
            )
            output, other = process.wait_output()
            logger.info(f"output 1.1: {output}")
            logger.info(f"other 1.1: {other}")
            for attempt in Retrying(stop=stop_after_attempt(5), wait=wait_fixed(3)):
                with attempt:
                    self.charm._patroni.reload_patroni_configuration()
                    # sleep(10)
                    process = container.exec(
                        [
                            "pgbackrest",
                            "--stanza=main",
                            "check",
                        ],
                        user=WORKLOAD_OS_USER,
                        group=WORKLOAD_OS_GROUP,
                    )
                    output, other = process.wait_output()
                    logger.info(f"output 1: {output}")
                    logger.info(f"other 1: {other}")
            # event.set_results({"backup-status": "backup started"})
            self.charm.unit.status = ActiveStatus()
            return True
        except RetryError as e:
            # event.fail(f"Failed to backup PostgreSQL with error: {str(e)}")
            logger.exception(e)
            self.charm.unit.status = BlockedStatus(
                f"failed to initialize stanza with error {str(e)}"
            )
            return False

    def _render_pgbackrest_conf_file(self, credentials: dict) -> None:
        """TODO."""
        # Open the template postgresql.conf file.
        with open("templates/pgbackrest.conf.j2", "r") as file:
            template = Template(file.read())
        # Render the template file with the correct values.
        logger.error(f"credentials: {credentials}")
        rendered = template.render(
            path=credentials["path"],
            region=credentials.get("region"),
            endpoint=credentials["endpoint"],
            bucket=credentials["bucket"],
            access_key=credentials["access-key"],
            secret_key=credentials["secret-key"],
            user=BACKUP_USER,
        )
        container = self.charm.unit.get_container("postgresql")
        filename = "/etc/pgbackrest.conf"
        container.remove_path(filename)
        container.push(
            filename,
            rendered,
            user=WORKLOAD_OS_USER,
            group=WORKLOAD_OS_GROUP,
        )
