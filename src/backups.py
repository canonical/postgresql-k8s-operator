# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Backups implementation."""
import json
import logging
import os
import re
import tempfile
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import boto3 as boto3
import botocore
from charms.data_platform_libs.v0.s3 import CredentialsChangedEvent, S3Requirer
from jinja2 import Template
from lightkube import ApiError, Client
from lightkube.resources.core_v1 import Endpoints
from ops.charm import ActionEvent
from ops.framework import Object
from ops.jujuversion import JujuVersion
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus
from ops.pebble import ChangeError, ExecError
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
        self.container = self.charm.unit.get_container("postgresql")

        # s3 relation handles the config options for s3 backups
        self.s3_client = S3Requirer(self.charm, self.relation_name)
        self.framework.observe(
            self.s3_client.on.credentials_changed, self._on_s3_credential_changed
        )
        self.framework.observe(self.charm.on.create_backup_action, self._on_create_backup_action)
        self.framework.observe(self.charm.on.list_backups_action, self._on_list_backups_action)
        self.framework.observe(self.charm.on.restore_action, self._on_restore_action)

    def _are_backup_settings_ok(self) -> Tuple[bool, Optional[str]]:
        """Validates whether backup settings are OK."""
        if self.model.get_relation(self.relation_name) is None:
            return (
                False,
                "Relation with s3-integrator charm missing, cannot create/restore backup.",
            )

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

    def _construct_endpoint(self, s3_parameters: Dict) -> str:
        """Construct the S3 service endpoint using the region.

        This is needed when the provided endpoint is from AWS, and it doesn't contain the region.
        """
        # Use the provided endpoint if a region is not needed.
        endpoint = s3_parameters["endpoint"]

        # Load endpoints data.
        loader = botocore.loaders.create_loader()
        data = loader.load_data("endpoints")

        # Construct the endpoint using the region.
        resolver = botocore.regions.EndpointResolver(data)
        endpoint_data = resolver.construct_endpoint("s3", s3_parameters["region"])

        # Use the built endpoint if it is an AWS endpoint.
        if endpoint_data and endpoint.endswith(endpoint_data["dnsSuffix"]):
            endpoint = f'{endpoint.split("://")[0]}://{endpoint_data["hostname"]}'

        return endpoint

    def _empty_data_files(self) -> None:
        """Empty the PostgreSQL data directory in preparation of backup restore."""
        try:
            self.container.exec(
                "rm -r /var/lib/postgresql/data/pgdata".split(),
                user="postgres",
                group="postgres",
            ).wait_output()
        except ExecError as e:
            logger.exception(
                "Failed to empty data directory in prep for backup restore", exc_info=e
            )
            raise

    def _execute_command(self, command: List[str]) -> Tuple[str, str]:
        """Execute a command in the workload container."""
        return self.container.exec(
            command,
            user=WORKLOAD_OS_USER,
            group=WORKLOAD_OS_GROUP,
        ).wait_output()

    def _format_backup_list(self, backup_list) -> str:
        """Formats provided list of backups as a table."""
        backups = ["{:<21s} | {:<12s} | {:s}".format("backup-id", "backup-type", "backup-status")]
        backups.append("-" * len(backups[0]))
        for backup_id, backup_type, backup_status in backup_list:
            backups.append(
                "{:<21s} | {:<12s} | {:s}".format(backup_id, backup_type, backup_status)
            )
        return "\n".join(backups)

    def _generate_backup_list_output(self) -> str:
        """Generates a list of backups in a formatted table.

        List contains successful and failed backups in order of ascending time.
        """
        backup_list = []
        output, _ = self._execute_command(["pgbackrest", "info", "--output=json"])
        backups = json.loads(output)[0]["backup"]
        for backup in backups:
            backup_id = datetime.strftime(
                datetime.strptime(backup["label"][:-1], "%Y%m%d-%H%M%S"), "%Y-%m-%dT%H:%M:%SZ"
            )
            error = backup["error"]
            backup_status = "finished"
            if error:
                backup_status = f"failed: {error}"
            backup_list.append((backup_id, "physical", backup_status))
        return self._format_backup_list(backup_list)

    def _list_backups_ids(self) -> List[str]:
        """Retrieve the list of backup ids."""
        output, _ = self._execute_command(["pgbackrest", "info", "--output=json"])
        backups = json.loads(output)[0]["backup"]
        return [
            datetime.strftime(
                datetime.strptime(backup["label"][:-1], "%Y%m%d-%H%M%S"), "%Y-%m-%dT%H:%M:%SZ"
            )
            for backup in backups
        ]

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
        except ExecError as e:
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

        # Retrieve the S3 Parameters to use when uploading the backup logs to S3.
        s3_parameters, _ = self._retrieve_s3_parameters()

        # Test uploading metadata to S3 to test credentials before backup.
        datetime_backup_requested = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        juju_version = JujuVersion.from_environ()
        metadata = f"""Date Backup Requested: {datetime_backup_requested}
Model Name: {self.model.name}
Application Name: {self.model.app.name}
Unit Name: {self.charm.unit.name}
Juju Version: {str(juju_version)}
"""
        if not self._upload_content_to_s3(
            metadata,
            os.path.join(
                s3_parameters["path"],
                f"backup/{self.charm.cluster_name}/latest",
            ),
            s3_parameters,
        ):
            event.fail("Failed to upload metadata to provided S3")
            return

        try:
            self.charm.unit.status = MaintenanceStatus("creating backup")
            stdout, stderr = self._execute_command(
                [
                    "pgbackrest",
                    f"--stanza={self.charm.cluster_name}",
                    "--log-level-console=debug",
                    "--type=full",
                    "backup",
                ]
            )
            backup_id = self._list_backups_ids()[-1]
        except ExecError as e:
            logger.exception(e)

            # Recover the backup id from the logs.
            backup_label_stdout_line = re.findall(
                r"(new backup label = )([0-9]{8}[-][0-9]{6}[F])$", e.stdout, re.MULTILINE
            )
            if len(backup_label_stdout_line) > 0:
                backup_id = backup_label_stdout_line[0][1]
            else:
                # Generate a backup id from the current date and time if the backup failed before
                # generating the backup label (our backup id).
                backup_id = datetime.strftime(datetime.now(), "%Y%m%d-%H%M%SF")

            # Upload the logs to S3.
            logs = f"""Stdout:
{e.stdout}

Stderr:
{e.stderr}
"""
            self._upload_content_to_s3(
                logs,
                os.path.join(
                    s3_parameters["path"],
                    f"backup/{self.charm.cluster_name}/{backup_id}/backup.log",
                ),
                s3_parameters,
            )
            event.fail(f"Failed to backup PostgreSQL with error: {str(e)}")
        else:
            # Upload the logs to S3 and fail the action if it doesn't succeed.
            logs = f"""Stdout:
{stdout}

Stderr:
{stderr}
"""
            if not self._upload_content_to_s3(
                logs,
                os.path.join(
                    s3_parameters["path"],
                    f"backup/{self.charm.cluster_name}/{backup_id}/backup.log",
                ),
                s3_parameters,
            ):
                event.fail("Error uploading logs to S3")
            else:
                event.set_results({"backup-status": "backup created"})

        self.charm.unit.status = ActiveStatus()

    def _on_list_backups_action(self, event) -> None:
        """List the previously created backups."""
        are_backup_settings_ok, validation_message = self._are_backup_settings_ok()
        if not are_backup_settings_ok:
            logger.warning(validation_message)
            event.fail(validation_message)
            return

        try:
            formatted_list = self._generate_backup_list_output()
            event.set_results({"backups": formatted_list})
        except ExecError as e:
            logger.exception(e)
            event.fail(f"Failed to list PostgreSQL backups with error: {str(e)}")

    def _on_restore_action(self, event):
        """Request that pgBackRest restores a backup."""
        if not self._pre_restore_checks(event):
            return

        backup_id = event.params.get("backup-id")
        logger.info(f"A restore with backup-id {backup_id} has been requested on unit")

        # Validate the provided backup id.
        logger.info("Validating provided backup-id")
        if backup_id not in self._list_backups_ids():
            event.fail(f"Invalid backup-id: {backup_id}")
            return

        self.charm.unit.status = MaintenanceStatus("restoring backup")
        error_message = "Failed to restore backup"

        # Stop the database service before performing the restore.
        logger.info("Stopping database service")
        try:
            self.container.stop(self.charm._postgresql_service)
        except ChangeError as e:
            logger.warning(f"Failed to stop database service with error: {str(e)}")
            event.fail(error_message)
            return

        # Delete the K8S endpoints that tracks the cluster information, including its id.
        # This is the same as "patronictl remove patroni-postgresql-k8s", but the latter doesn't
        # work after the database service is stopped on Pebble.
        logger.info("Removing previous cluster information")
        try:
            client = Client()
            client.delete(
                Endpoints,
                name=f"patroni-{self.charm._name}",
                namespace=self.charm._namespace,
            )
            client.delete(
                Endpoints,
                name=f"patroni-{self.charm._name}-config",
                namespace=self.charm._namespace,
            )
        except ApiError as e:
            logger.warning(f"Failed to remove previous cluster information with error: {str(e)}")
            event.fail(error_message)
            self._restart_database()
            return

        logger.info("Removing the contents of the data directory")
        try:
            self._empty_data_files()
        except ExecError as e:
            logger.warning(f"Failed to remove contents of the data directory with error: {str(e)}")
            event.fail(error_message)
            self._restart_database()
            return

        # Mark the cluster as in a restoring backup state and update the Patroni configuration.
        logger.info("Configuring Patroni to restore the backup")
        self.charm.app_peer_data.update(
            {
                "archive-mode": "off",
                "restoring-backup": f'{datetime.strftime(datetime.strptime(backup_id, "%Y-%m-%dT%H:%M:%SZ"), "%Y%m%d-%H%M%S")}F',
            }
        )
        self.charm.update_config()

        # Start the database to start the restore process.
        logger.info("Configuring Patroni to restore the backup")
        self.container.start(self.charm._postgresql_service)

        event.set_results({"restore-status": "restore started"})

    def _pre_restore_checks(self, event: ActionEvent) -> bool:
        """Run some checks before starting the restore.

        Returns:
            a boolean indicating whether restore should be run.
        """
        if not event.params.get("backup-id"):
            event.fail("Missing backup-id to restore")
            return False

        if not self.container.can_connect():
            error_message = "Workload container not ready yet!"
            logger.warning(error_message)
            event.fail(error_message)
            return False

        logger.info("Checking if cluster is in blocked state")
        if self.charm.is_blocked:
            error_message = "Cluster or unit is in a blocking state"
            logger.warning(error_message)
            event.fail(error_message)
            return False

        logger.info("Checking that the cluster does not have more than one unit")
        if self.charm.app.planned_units() > 1:
            error_message = (
                "Unit cannot restore backup as there are more than one unit in the cluster"
            )
            logger.warning(error_message)
            event.fail(error_message)
            return False

        return True

    def _render_pgbackrest_conf_file(self, s3_parameters: Dict) -> None:
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
        filename = "/etc/pgbackrest.conf"
        self.container.push(
            filename,
            rendered,
            user=WORKLOAD_OS_USER,
            group=WORKLOAD_OS_GROUP,
        )

    def _restart_database(self) -> None:
        """Removes the restoring backup flag and restart the database."""
        self.charm.app_peer_data.update({"archive-mode": "", "restoring-backup": ""})
        self.charm.update_config()
        self.container.start(self.charm._postgresql_service)

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

    def _upload_content_to_s3(
        self: str,
        content: str,
        s3_path: str,
        s3_parameters: Dict,
    ) -> bool:
        """Uploads the provided contents to the provided S3 bucket.

        Args:
            content: The content to upload to S3
            s3_path: The path to which to upload the content
            s3_parameters: A dictionary containing the S3 parameters
                The following are expected keys in the dictionary: bucket, region,
                endpoint, access-key and secret-key

        Returns:
            a boolean indicating success.
        """
        bucket_name = s3_parameters["bucket"]
        s3_path = os.path.join(s3_parameters["path"], s3_path).lstrip("/")
        logger.info(f"Uploading content to bucket={s3_parameters['bucket']}, path={s3_path}")
        try:
            logger.info(f"Uploading content to bucket={bucket_name}, path={s3_path}")
            session = boto3.session.Session(
                aws_access_key_id=s3_parameters["access-key"],
                aws_secret_access_key=s3_parameters["secret-key"],
                region_name=s3_parameters["region"],
            )

            s3 = session.resource("s3", endpoint_url=self._construct_endpoint(s3_parameters))
            bucket = s3.Bucket(bucket_name)

            with tempfile.NamedTemporaryFile() as temp_file:
                temp_file.write(content.encode("utf-8"))
                temp_file.flush()
                bucket.upload_file(temp_file.name, s3_path)
        except Exception as e:
            logger.exception(
                f"Failed to upload content to S3 bucket={bucket_name}, path={s3_path}", exc_info=e
            )
            return False

        return True
