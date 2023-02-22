# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Backups implementation."""

import logging
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

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

    def _are_backup_settings_ok(self) -> Tuple[bool, Optional[str]]:
        """Validates whether backup settings are OK."""
        if self.model.get_relation(self.relation_name) is None:
            return False, "Relation with s3-integrator charm missing, cannot create backup."

        s3_parameters, missing_parameters = self._retrieve_s3_parameters()
        if missing_parameters:
            return False, f"Missing S3 parameters: {missing_parameters}"

        if "stanza" not in self.charm._peers.data[self.charm.unit]:
            return False, "Stanza was not initialised"

        return True, None

    def _can_unit_perform_backup(self) -> Tuple[bool, Optional[str]]:
        """Validates whether this unit can perform a backup."""
        if self.charm.is_blocked:
            return False, "Unit is in a blocking state"

        if (
            self.charm.unit.name == self.charm._patroni.get_primary(unit_name_pattern=True)
            and self.charm.app.planned_units() > 1
        ):
            return False, "Unit cannot perform backups as it is the cluster primary"

        if not self.charm._patroni.member_started:
            return False, "Unit cannot perform backups as it's not in running state"

        return self._are_backup_settings_ok()

    def _execute_command(self, command: List[str]) -> str:
        """Execute a command in the workload container."""
        container = self.charm.unit.get_container("postgresql")
        process = container.exec(
            command,
            user=WORKLOAD_OS_USER,
            group=WORKLOAD_OS_GROUP,
        )
        output, _ = process.wait_output()
        return output

    def _initialise_stanza(self) -> None:
        """Initialize the stanza.

        A stanza is the configuration for a PostgreSQL database cluster that defines where it is
        located, how it will be backed up, archiving options, etc. (more info in
        https://pgbackrest.org/user-guide.html#quickstart/configure-stanza).
        """
        if self.charm.is_blocked:
            logger.warning("couldn't initialize stanza due to a blocked status")
            return

        self.charm.unit.status = MaintenanceStatus("initialising stanza")

        try:
            # Create the stanza.
            self._execute_command(
                ["pgbackrest", f"--stanza={self.charm.cluster_name}", "stanza-create"]
            )
        except pebble.ExecError as e:
            logger.exception(e)
            self.charm.unit.status = BlockedStatus(
                f"failed to initialize stanza with error {str(e)}"
            )
            return

        # Store the stanza name to be used in configurations updates.
        self.charm._peers.data[self.charm.unit].update({"stanza": self.charm.cluster_name})

        # Update the configuration to use pgBackRest as the archiving mechanism.
        self.charm.update_config()

        try:
            # Check that the stanza is correctly configured.
            for attempt in Retrying(stop=stop_after_attempt(5), wait=wait_fixed(3)):
                with attempt:
                    self.charm._patroni.reload_patroni_configuration()
                    self._execute_command(
                        ["pgbackrest", f"--stanza={self.charm.cluster_name}", "check"]
                    )
            self.charm.unit.status = ActiveStatus()
        except RetryError as e:
            logger.exception(e)
            self.charm.unit.status = BlockedStatus(
                f"failed to initialize stanza with error {str(e)}"
            )

    def _on_s3_credential_changed(self, event: CredentialsChangedEvent):
        """Call the stanza initialization when the credentials or the connection info change."""
        if "cluster_initialised" not in self.charm.app_peer_data:
            logger.debug("Cannot set pgBackRest configurations, PostgreSQL has not yet started.")
            event.defer()
            return

        s3_parameters, missing_parameters = self._retrieve_s3_parameters()
        if missing_parameters:
            logger.warning(
                f"Cannot set pgBackRest configurations due to missing S3 parameters: {missing_parameters}"
            )
            return

        self._render_pgbackrest_conf_file(s3_parameters)
        self._initialise_stanza()

    def _on_create_backup_action(self, event) -> None:
        """Request that pgBackRest creates a backup."""
        can_unit_perform_backup, validation_message = self._can_unit_perform_backup()
        if not can_unit_perform_backup:
            logger.warning(validation_message)
            event.fail(validation_message)
            return

        try:
            self.charm.unit.status = MaintenanceStatus("creating backup")
            self.charm.patch_pod_labels(self.charm.unit.name, add_labels=False)
            self._execute_command(
                ["pgbackrest", f"--stanza={self.charm.cluster_name}", "--type=full", "backup"]
            )
            event.set_results({"backup-status": "backup created"})
        except pebble.ExecError as e:
            event.fail(f"Failed to backup PostgreSQL with error: {str(e)}")
        self.charm.patch_pod_labels(self.charm.unit.name)
        self.charm.unit.status = ActiveStatus()

    def _on_list_backups_action(self, event) -> None:
        """List the previously created backups."""
        are_backup_settings_ok, validation_message = self._are_backup_settings_ok()
        if not are_backup_settings_ok:
            logger.warning(validation_message)
            event.fail(validation_message)
            return

        try:
            output = self._execute_command(
                ["pgbackrest", "repo-ls", f"backup/{self.charm.cluster_name}"]
            )
            backup_ids = re.findall(r".*[F]$", output, re.MULTILINE)
            backup_ids = [
                datetime.strftime(
                    datetime.strptime(backup_id[:-1], "%Y%m%d-%H%M%S"), "%Y-%m-%dT%H:%M:%SZ"
                )
                for backup_id in backup_ids
            ]
            event.set_results({"backup-list": backup_ids})
        except pebble.ExecError as e:
            logger.exception(e)
            event.fail(f"Failed to list PostgreSQL backups with error: {str(e)}")

    def _render_pgbackrest_conf_file(self, s3_parameters: dict) -> None:
        """Render the pgBackRest configuration file."""
        # Open the template pgbackrest.conf file.
        with open("templates/pgbackrest.conf.j2", "r") as file:
            template = Template(file.read())
        # Render the template file with the correct values.
        rendered = template.render(
            path=s3_parameters["path"],
            region=s3_parameters.get("region"),
            endpoint=s3_parameters["endpoint"],
            bucket=s3_parameters["bucket"],
            s3_uri_style=s3_parameters["s3-uri-style"],
            access_key=s3_parameters["access-key"],
            secret_key=s3_parameters["secret-key"],
            stanza=self.charm.cluster_name,
            user=BACKUP_USER,
        )
        # Delete the original file and render the one with the right info.
        container = self.charm.unit.get_container("postgresql")
        filename = "/etc/pgbackrest.conf"
        container.remove_path(filename)
        container.push(
            filename,
            rendered,
            user=WORKLOAD_OS_USER,
            group=WORKLOAD_OS_GROUP,
        )

    def _retrieve_s3_parameters(self) -> Tuple[Dict, List[str]]:
        """Retrieve S3 parameters from the S3 integrator relation."""
        s3_parameters = self.s3_client.get_s3_connection_info()
        required_parameters = [
            "bucket",
            "access-key",
            "secret-key",
        ]
        missing_required_parameters = [
            param for param in required_parameters if param not in s3_parameters
        ]
        if missing_required_parameters:
            logger.warning(
                f"Missing required S3 parameters in relation with S3 integrator: {missing_required_parameters}"
            )
            return {}, missing_required_parameters

        # Add some sensible defaults (as expected by the code) for missing optional parameters
        s3_parameters.setdefault("endpoint", "https://s3.amazonaws.com")
        s3_parameters.setdefault("region")
        s3_parameters.setdefault("path", "")
        s3_parameters.setdefault("s3-uri-style", "host")

        return s3_parameters, []
