# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Backups implementation."""

import json
import logging
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from io import BytesIO

import boto3 as boto3
import botocore
from botocore.exceptions import ClientError
from charms.data_platform_libs.v0.s3 import CredentialsChangedEvent, S3Requirer
from jinja2 import Template
from lightkube import ApiError, Client
from lightkube.resources.core_v1 import Endpoints
from ops import HookEvent
from ops.charm import ActionEvent
from ops.framework import Object
from ops.jujuversion import JujuVersion
from ops.model import ActiveStatus, MaintenanceStatus
from ops.pebble import ChangeError, ExecError
from tenacity import RetryError, Retrying, stop_after_attempt, wait_fixed

from constants import (
    BACKUP_TYPE_OVERRIDES,
    BACKUP_USER,
    PGBACKREST_LOGROTATE_FILE,
    WORKLOAD_OS_GROUP,
    WORKLOAD_OS_USER,
)
from relations.async_replication import REPLICATION_CONSUMER_RELATION, REPLICATION_OFFER_RELATION

logger = logging.getLogger(__name__)

ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE = "the S3 repository has backups from another cluster"
FAILED_TO_ACCESS_CREATE_BUCKET_ERROR_MESSAGE = (
    "failed to access/create the bucket, check your S3 settings"
)
FAILED_TO_INITIALIZE_STANZA_ERROR_MESSAGE = "failed to initialize stanza, check your S3 settings"
CANNOT_RESTORE_PITR = "cannot restore PITR, juju debug-log for details"

S3_BLOCK_MESSAGES = [
    ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE,
    FAILED_TO_ACCESS_CREATE_BUCKET_ERROR_MESSAGE,
    FAILED_TO_INITIALIZE_STANZA_ERROR_MESSAGE,
]


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
        # When the leader unit is being removed, s3_client.on.credentials_gone is performed on it (and only on it).
        # After a new leader is elected, the S3 connection must be reinitialized.
        self.framework.observe(self.charm.on.leader_elected, self._on_s3_credential_changed)
        self.framework.observe(self.s3_client.on.credentials_gone, self._on_s3_credential_gone)
        self.framework.observe(self.charm.on.create_backup_action, self._on_create_backup_action)
        self.framework.observe(self.charm.on.list_backups_action, self._on_list_backups_action)
        self.framework.observe(self.charm.on.restore_action, self._on_restore_action)

    @property
    def stanza_name(self) -> str:
        """Stanza name, composed by model and cluster name."""
        return f"{self.model.name}.{self.charm.cluster_name}"

    @property
    def _tls_ca_chain_filename(self) -> str:
        """Returns the path to the TLS CA chain file."""
        s3_parameters, _ = self._retrieve_s3_parameters()
        if s3_parameters.get("tls-ca-chain") is not None:
            return f"{self.charm._storage_path}/pgbackrest-tls-ca-chain.crt"
        return ""

    def _are_backup_settings_ok(self) -> tuple[bool, str | None]:
        """Validates whether backup settings are OK."""
        if self.model.get_relation(self.relation_name) is None:
            return (
                False,
                "Relation with s3-integrator charm missing, cannot create/restore backup.",
            )

        _, missing_parameters = self._retrieve_s3_parameters()
        if missing_parameters:
            return False, f"Missing S3 parameters: {missing_parameters}"

        return True, None

    @property
    def _can_initialise_stanza(self) -> bool:
        """Validates whether this unit can initialise a stanza."""
        # Don't allow stanza initialisation if this unit hasn't started the database
        # yet and either hasn't joined the peer relation yet or hasn't configured TLS
        # yet while other unit already has TLS enabled.
        return not (
            not self.charm._patroni.member_started
            and (
                (len(self.charm._peers.data.keys()) == 2)
                or (
                    "tls" not in self.charm.unit_peer_data
                    and any("tls" in unit_data for _, unit_data in self.charm._peers.data.items())
                )
            )
        )

    def _can_unit_perform_backup(self) -> tuple[bool, str | None]:
        """Validates whether this unit can perform a backup."""
        if self.charm.is_blocked:
            return False, "Unit is in a blocking state"

        tls_enabled = "tls" in self.charm.unit_peer_data

        # Check if this unit is the primary (if it was not possible to retrieve that information,
        # then show that the unit cannot perform a backup, because possibly the database is offline).
        try:
            is_primary = self.charm.is_primary
        except RetryError:
            return False, "Unit cannot perform backups as the database seems to be offline"

        # Only enable backups on primary if there are replicas but TLS is not enabled.
        if is_primary and self.charm.app.planned_units() > 1 and tls_enabled:
            return False, "Unit cannot perform backups as it is the cluster primary"

        # Can create backups on replicas only if TLS is enabled (it's needed to enable
        # pgBackRest to communicate with the primary to request that missing WAL files
        # are pushed to the S3 repo before the backup action is triggered).
        if not is_primary and not tls_enabled:
            return False, "Unit cannot perform backups as TLS is not enabled"

        if not self.charm._patroni.member_started:
            return False, "Unit cannot perform backups as it's not in running state"

        if "stanza" not in self.charm.app_peer_data:
            return False, "Stanza was not initialised"

        return self._are_backup_settings_ok()

    def can_use_s3_repository(self) -> tuple[bool, str | None]:
        """Returns whether the charm was configured to use another cluster repository."""
        # Check model uuid
        s3_parameters, _ = self._retrieve_s3_parameters()
        s3_model_uuid = self._read_content_from_s3(
            "model-uuid.txt",
            s3_parameters,
        )
        if s3_model_uuid and s3_model_uuid.strip() != self.model.uuid:
            logger.debug(
                f"can_use_s3_repository: incompatible model-uuid s3={s3_model_uuid.strip()}, local={self.model.uuid}"
            )
            return False, ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE

        output, _ = self._execute_command(["pgbackrest", "info", "--output=json"], timeout=30)
        if output is None:
            return False, FAILED_TO_INITIALIZE_STANZA_ERROR_MESSAGE

        for stanza in json.loads(output):
            if stanza.get("name") != self.stanza_name:
                logger.debug(
                    f"can_use_s3_repository: incompatible stanza name s3={stanza.get('name', '')}, local={self.stanza_name}"
                )
                return False, ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE

            system_identifier_from_instance, error = self._execute_command([
                f"/usr/lib/postgresql/{self.charm._patroni.rock_postgresql_version.split('.')[0]}/bin/pg_controldata",
                "/var/lib/postgresql/data/pgdata",
            ])
            if error != "":
                raise Exception(error)
            system_identifier_from_instance = next(
                line
                for line in system_identifier_from_instance.splitlines()
                if "Database system identifier" in line
            ).split(" ")[-1]
            system_identifier_from_stanza = str(stanza.get("db")[0]["system-id"])
            if system_identifier_from_instance != system_identifier_from_stanza:
                logger.debug(
                    f"can_use_s3_repository: incompatible system identifier s3={system_identifier_from_stanza}, local={system_identifier_from_instance}"
                )
                return False, ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE
        return True, None

    def _construct_endpoint(self, s3_parameters: dict) -> str:
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
            endpoint = f"{endpoint.split('://')[0]}://{endpoint_data['hostname']}"

        return endpoint

    def _create_bucket_if_not_exists(self) -> None:
        s3_parameters, missing_parameters = self._retrieve_s3_parameters()
        if missing_parameters:
            return

        bucket_name = s3_parameters["bucket"]
        region = s3_parameters.get("region")
        session = boto3.session.Session(
            aws_access_key_id=s3_parameters["access-key"],
            aws_secret_access_key=s3_parameters["secret-key"],
            region_name=s3_parameters["region"],
        )

        try:
            s3 = session.resource(
                "s3",
                endpoint_url=self._construct_endpoint(s3_parameters),
                verify=(self._tls_ca_chain_filename or None),
            )
        except ValueError as e:
            logger.exception("Failed to create a session '%s' in region=%s.", bucket_name, region)
            raise e
        bucket = s3.Bucket(bucket_name)
        try:
            bucket.meta.client.head_bucket(Bucket=bucket_name)
            logger.info("Bucket %s exists.", bucket_name)
            exists = True
        except ClientError:
            logger.warning("Bucket %s doesn't exist or you don't have access to it.", bucket_name)
            exists = False
        if not exists:
            try:
                bucket.create(CreateBucketConfiguration={"LocationConstraint": region})

                bucket.wait_until_exists()
                logger.info("Created bucket '%s' in region=%s", bucket_name, region)
            except ClientError as error:
                logger.exception(
                    "Couldn't create bucket named '%s' in region=%s.", bucket_name, region
                )
                raise error

    def _empty_data_files(self) -> None:
        """Empty the PostgreSQL data directory in preparation of backup restore."""
        try:
            self.container.exec("rm -r /var/lib/postgresql/data/pgdata".split()).wait_output()
        except ExecError as e:
            # If previous PITR restore was unsuccessful, there is no such directory.
            if "No such file or directory" not in e.stderr:
                logger.exception(
                    "Failed to empty data directory in prep for backup restore", exc_info=e
                )
                raise

    def _change_connectivity_to_database(self, connectivity: bool) -> None:
        """Enable or disable the connectivity to the database."""
        self.charm.unit_peer_data.update({"connectivity": "on" if connectivity else "off"})
        self.charm.update_config(is_creating_backup=True)

    def _execute_command(
        self,
        command: list[str],
        timeout: float | None = None,
        stream: bool = False,
    ) -> tuple[str | None, str | None]:
        """Execute a command in the workload container."""
        try:
            logger.debug("Running command %s", " ".join(command))
            process = self.container.exec(
                command,
                user=WORKLOAD_OS_USER,
                group=WORKLOAD_OS_GROUP,
                timeout=timeout,
            )
            if not stream:
                return process.wait_output()

            stdout = stderr = ""
            # Read from stdout's IO stream directly, unbuffered
            for line in process.stdout:
                logger.debug("Captured stdout: \n%s", repr(line))
                stdout += line
            # Fetch from stderr afterwards (not in real time)
            for line in process.stderr:
                logger.debug("Captured stderr: \n%s", repr(line))
                stderr += line
            process.wait()
            return stdout, stderr

        except ChangeError:
            return None, None

    def _format_backup_list(self, backup_list) -> str:
        """Formats provided list of backups as a table."""
        s3_parameters, _ = self._retrieve_s3_parameters()
        backups = [
            "Storage bucket name: {:s}".format(s3_parameters["bucket"]),
            "Backups base path: {:s}/backup/\n".format(s3_parameters["path"]),
            "{:<20s} | {:<19s} | {:<8s} | {:<20s} | {:<23s} | {:<20s} | {:<20s} | {:<8s} | {:s}".format(
                "backup-id",
                "action",
                "status",
                "reference-backup-id",
                "LSN start/stop",
                "start-time",
                "finish-time",
                "timeline",
                "backup-path",
            ),
        ]
        backups.append("-" * len(backups[2]))
        for (
            backup_id,
            backup_action,
            backup_status,
            reference,
            lsn_start_stop,
            start,
            stop,
            backup_timeline,
            path,
        ) in backup_list:
            backups.append(
                f"{backup_id:<20s} | {backup_action:<19s} | {backup_status:<8s} | {reference:<20s} | {lsn_start_stop:<23s} | {start:<20s} | {stop:<20s} | {backup_timeline:<8s} | {path:s}"
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
            backup_id, backup_type = self._parse_backup_id(backup["label"])
            backup_action = f"{backup_type} backup"
            backup_reference = "None"
            if backup["reference"]:
                backup_reference, _ = self._parse_backup_id(backup["reference"][-1])
            lsn_start_stop = f"{backup['lsn']['start']} / {backup['lsn']['stop']}"
            time_start, time_stop = (
                datetime.strftime(
                    datetime.fromtimestamp(stamp, timezone.utc), "%Y-%m-%dT%H:%M:%SZ"
                )
                for stamp in backup["timestamp"].values()
            )
            backup_timeline = (
                backup["archive"]["start"][:8].lstrip("0")
                if backup["archive"] and backup["archive"]["start"]
                else ""
            )
            backup_path = f"/{self.stanza_name}/{backup['label']}"
            error = backup["error"]
            backup_status = "finished"
            if error:
                backup_status = f"failed: {error}"
            backup_list.append((
                backup_id,
                backup_action,
                backup_status,
                backup_reference,
                lsn_start_stop,
                time_start,
                time_stop,
                backup_timeline,
                backup_path,
            ))

        for timeline, (_, timeline_id) in self._list_timelines().items():
            backup_list.append((
                timeline,
                "restore",
                "finished",
                "None",
                "n/a",
                timeline,
                "n/a",
                timeline_id,
                "n/a",
            ))

        backup_list.sort(key=lambda x: x[0])

        return self._format_backup_list(backup_list)

    def _list_backups(self, show_failed: bool, parse=True) -> dict[str, tuple[str, str]]:
        """Retrieve the list of backups.

        Args:
            show_failed: whether to also return the failed backups.
            parse: whether to convert backup labels to their IDs or not.

        Returns:
            a dict of previously created backups: id => (stanza, timeline) or an empty dict if there is no backups in
                the S3 bucket.
        """
        output, _ = self._execute_command(["pgbackrest", "info", "--output=json"])
        repository_info = next(iter(json.loads(output)), None)

        # If there are no backups, returns an empty dict.
        if repository_info is None:
            return dict[str, tuple[str, str]]()

        backups = repository_info["backup"]
        stanza_name = repository_info["name"]
        return dict[str, tuple[str, str]]({
            self._parse_backup_id(backup["label"])[0] if parse else backup["label"]: (
                stanza_name,
                backup["archive"]["start"][:8].lstrip("0")
                if backup["archive"] and backup["archive"]["start"]
                else "",
            )
            for backup in backups
            if show_failed or not backup["error"]
        })

    def _list_timelines(self) -> dict[str, tuple[str, str]]:
        """Lists the timelines from the pgBackRest stanza.

        Returns:
            a dict of timelines: id => (stanza, timeline) or an empty dict if there is no timelines in the S3 bucket.
        """
        output, _ = self._execute_command([
            "pgbackrest",
            "repo-ls",
            "--recurse",
            "--output=json",
        ])

        repository = json.loads(output).items()
        if repository is None:
            return dict[str, tuple[str, str]]()

        return dict[str, tuple[str, str]]({
            datetime.strftime(
                datetime.fromtimestamp(timeline_object["time"], timezone.utc),
                "%Y-%m-%dT%H:%M:%SZ",
            ): (
                timeline.split("/")[1],
                timeline.split("/")[-1].split(".")[0].lstrip("0"),
            )
            for timeline, timeline_object in repository
            if timeline.endswith(".history") and not timeline.endswith("backup.history")
        })

    def _get_nearest_timeline(self, timestamp: str) -> tuple[str, str] | None:
        """Finds the nearest timeline or backup prior to the specified timeline.

        Returns:
            (stanza, timeline) of the nearest timeline or backup. None, if there are no matches.
        """
        timelines = self._list_backups(show_failed=False) | self._list_timelines()
        if timestamp == "latest":
            return max(timelines.items())[1] if len(timelines) > 0 else None
        filtered_timelines = [
            (timeline_key, timeline_object)
            for timeline_key, timeline_object in timelines.items()
            if datetime.strptime(timeline_key, "%Y-%m-%dT%H:%M:%SZ")
            <= self._parse_psql_timestamp(timestamp)
        ]
        return max(filtered_timelines)[1] if len(filtered_timelines) > 0 else None

    def _is_psql_timestamp(self, timestamp: str) -> bool:
        if not re.match(
            r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d{1,6})?([-+](?:\d{2}|\d{4}|\d{2}:\d{2}))?$",
            timestamp,
        ):
            return False
        try:
            self._parse_psql_timestamp(timestamp)
            return True
        except ValueError:
            return False

    def _parse_psql_timestamp(self, timestamp: str) -> datetime:
        """Intended to use with data only after _is_psql_timestamp check."""
        # With the python >= 3.11 only the datetime.fromisoformat will be sufficient without any regexes. Therefore,
        # it will not be required for the _is_psql_timestamp check that ensures intended regex execution.
        t = re.sub(r"([-+]\d{2})$", r"\1:00", timestamp)
        t = re.sub(r"([-+]\d{2})(\d{2})$", r"\1:\2", t)
        t = re.sub(r"\.(\d+)", lambda x: f".{x[1]:06}", t)
        dt = datetime.fromisoformat(t)
        # Convert to the timezone-naive
        if dt.tzinfo is not None and dt.tzinfo is not timezone.utc:
            dt = dt.astimezone(tz=timezone.utc)
        return dt.replace(tzinfo=None)

    def _parse_backup_id(self, label) -> tuple[str, str]:
        """Parse backup ID as a timestamp and its type."""
        if label[-1] == "F":
            timestamp = label
            backup_type = "full"
        elif label[-1] == "D":
            timestamp = label.split("_")[1]
            backup_type = "differential"
        elif label[-1] == "I":
            timestamp = label.split("_")[1]
            backup_type = "incremental"
        else:
            raise ValueError("Unknown label format for backup ID: %s", label)

        return (
            datetime.strftime(
                datetime.strptime(timestamp[:-1], "%Y%m%d-%H%M%S"), "%Y-%m-%dT%H:%M:%SZ"
            ),
            backup_type,
        )

    def _initialise_stanza(self, event: HookEvent) -> bool:
        """Initialize the stanza.

        A stanza is the configuration for a PostgreSQL database cluster that defines where it is
        located, how it will be backed up, archiving options, etc. (more info in
        https://pgbackrest.org/user-guide.html#quickstart/configure-stanza).
        """
        # Enable stanza initialisation if the backup settings were fixed after being invalid
        # or pointing to a repository where there are backups from another cluster.
        if self.charm.is_blocked and self.charm.unit.status.message not in S3_BLOCK_MESSAGES:
            logger.warning("couldn't initialize stanza due to a blocked status, deferring event")
            event.defer()
            return False

        self.charm.unit.status = MaintenanceStatus("initialising stanza")

        # Create the stanza.
        try:
            # If the tls is enabled, it requires all the units in the cluster to run the pgBackRest service to
            # successfully complete validation, and upon receiving the same parent event other units should start it.
            # Therefore, the first retry may fail due to the delay of these other units to start this service. 60s given
            # for that or else the s3 initialization sequence will fail.
            for attempt in Retrying(stop=stop_after_attempt(6), wait=wait_fixed(10), reraise=True):
                with attempt:
                    self._execute_command([
                        "pgbackrest",
                        f"--stanza={self.stanza_name}",
                        "stanza-create",
                    ])
        except ExecError as e:
            logger.exception(e)
            self._s3_initialization_set_failure(FAILED_TO_INITIALIZE_STANZA_ERROR_MESSAGE)
            return False

        self.start_stop_pgbackrest_service()

        # Rest of the successful s3 initialization sequence such as s3-initialization-start and s3-initialization-done
        # are left to the check_stanza func.
        if self.charm.unit.is_leader():
            self.charm.app_peer_data.update({
                "stanza": self.stanza_name,
            })
        else:
            self.charm.unit_peer_data.update({
                "stanza": self.stanza_name,
            })

        return True

    def check_stanza(self) -> bool:
        """Runs the pgbackrest stanza validation.

        Returns:
            whether stanza validation was successful.
        """
        # Update the configuration to use pgBackRest as the archiving mechanism.
        self.charm.update_config()

        self.charm.unit.status = MaintenanceStatus("checking stanza")

        try:
            # If the tls is enabled, it requires all the units in the cluster to run the pgBackRest service to
            # successfully complete validation, and upon receiving the same parent event other units should start it.
            # Therefore, the first retry may fail due to the delay of these other units to start this service. 60s given
            # for that or else the s3 initialization sequence will fail.
            for attempt in Retrying(stop=stop_after_attempt(6), wait=wait_fixed(10), reraise=True):
                with attempt:
                    self._execute_command(["pgbackrest", f"--stanza={self.stanza_name}", "check"])
            self.charm._set_active_status()
        except Exception as e:
            logger.exception(e)
            self._s3_initialization_set_failure(FAILED_TO_INITIALIZE_STANZA_ERROR_MESSAGE)
            return False

        if self.charm.unit.is_leader():
            self.charm.app_peer_data.update({
                "s3-initialization-start": "",
            })
        else:
            self.charm.unit_peer_data.update({"s3-initialization-done": "True"})

        return True

    def coordinate_stanza_fields(self) -> None:
        """Coordinate the stanza name between the primary and the leader units."""
        if (
            not self.charm.unit.is_leader()
            or "s3-initialization-start" not in self.charm.app_peer_data
        ):
            return

        for _unit, unit_data in self.charm._peers.data.items():
            if "s3-initialization-done" not in unit_data:
                continue

            self.charm.app_peer_data.update({
                "stanza": unit_data.get("stanza", ""),
                "s3-initialization-block-message": unit_data.get(
                    "s3-initialization-block-message", ""
                ),
                "s3-initialization-start": "",
                "s3-initialization-done": "True",
            })

            self.charm.update_config()
            break

    @property
    def _is_primary_pgbackrest_service_running(self) -> bool:
        """Returns whether the pgBackRest TLS server is running in the primary unit."""
        try:
            primary = self.charm._patroni.get_primary()
        except (RetryError, ConnectionError) as e:
            logger.error(f"failed to get primary with error {e!s}")
            return False

        if primary is None:
            logger.debug("the primary was not elected yet")
            return False

        primary_endpoint = self.charm._get_hostname_from_unit(primary)

        try:
            self._execute_command([
                "pgbackrest",
                "server-ping",
                "--io-timeout=10",
                primary_endpoint,
            ])
        except ExecError as e:
            logger.warning(
                f"Failed to contact pgBackRest TLS server on {primary_endpoint} with error {e!s}"
            )
            return False

        return True

    def _on_s3_credentials_checks(self, event: CredentialsChangedEvent) -> bool:
        if not self.s3_client.get_s3_connection_info():
            logger.debug("_on_s3_credential_changed early exit: no connection info")
            return False

        if not self.charm.is_cluster_initialised:
            logger.debug("Cannot set pgBackRest configurations, PostgreSQL has not yet started.")
            event.defer()
            return False

        # Prevents config change in bad state, so DB peer relations change event will not cause patroni related errors.
        if self.charm.unit.status.message == CANNOT_RESTORE_PITR:
            logger.info("Cannot change S3 configuration in bad PITR restore status")
            event.defer()
            return False

        # Prevents S3 change in the middle of restoring backup and patroni / pgbackrest errors caused by that.
        if self.charm.is_cluster_restoring_backup or self.charm.is_cluster_restoring_to_time:
            logger.info("Cannot change S3 configuration during restore")
            event.defer()
            return False

        if not self._render_pgbackrest_conf_file():
            logger.debug("Cannot set pgBackRest configurations, missing configurations.")
            return False

        if not self._can_initialise_stanza:
            logger.debug("Cannot initialise stanza yet.")
            event.defer()
            return False
        return True

    def _on_s3_credential_changed(self, event: CredentialsChangedEvent) -> bool | None:
        """Call the stanza initialization when the credentials or the connection info change."""
        if not self._on_s3_credentials_checks(event):
            return

        self.start_stop_pgbackrest_service()

        if self.charm.unit.is_leader():
            self.charm.app_peer_data.update({
                "s3-initialization-block-message": "",
                "s3-initialization-start": time.asctime(time.gmtime()),
                "stanza": "",
                "s3-initialization-done": "",
            })
            if not self.charm.is_primary:
                self.charm._set_active_status()

        if self.charm.is_primary and "s3-initialization-done" not in self.charm.unit_peer_data:
            self._on_s3_credential_changed_primary(event)

        if self.charm.is_standby_leader:
            logger.info(
                "S3 credentials will not be connected on standby cluster until it becomes primary"
            )

    def _on_s3_credential_changed_primary(self, event: HookEvent) -> bool:
        self.charm.update_config()

        try:
            self._create_bucket_if_not_exists()
        except (ClientError, ValueError):
            self._s3_initialization_set_failure(FAILED_TO_ACCESS_CREATE_BUCKET_ERROR_MESSAGE)
            return False

        can_use_s3_repository, validation_message = self.can_use_s3_repository()
        if not can_use_s3_repository:
            self._s3_initialization_set_failure(validation_message)
            return False

        if not self._initialise_stanza(event):
            return False

        if not self.check_stanza():
            return False

        s3_parameters, _ = self._retrieve_s3_parameters()
        self._upload_content_to_s3(
            self.model.uuid,
            "model-uuid.txt",
            s3_parameters,
        )

        return True

    def _on_create_backup_action(self, event) -> None:  # noqa: C901
        """Request that pgBackRest creates a backup."""
        backup_type = event.params.get("type", "full")
        if backup_type not in BACKUP_TYPE_OVERRIDES:
            error_message = f"Invalid backup type: {backup_type}. Possible values: {', '.join(BACKUP_TYPE_OVERRIDES.keys())}."
            logger.error(f"Backup failed: {error_message}")
            event.fail(error_message)
            return

        if (
            backup_type in ["differential", "incremental"]
            and len(self._list_backups(show_failed=False)) == 0
        ):
            error_message = (
                f"Invalid backup type: {backup_type}. No previous full backup to reference."
            )
            logger.error(f"Backup failed: {error_message}")
            event.fail(error_message)
            return

        logger.info(f"A {backup_type} backup has been requested on unit")
        can_unit_perform_backup, validation_message = self._can_unit_perform_backup()
        if not can_unit_perform_backup:
            logger.error(f"Backup failed: {validation_message}")
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
Juju Version: {juju_version!s}
"""
        if not self._upload_content_to_s3(
            metadata,
            f"backup/{self.stanza_name}/latest",
            s3_parameters,
        ):
            error_message = "Failed to upload metadata to provided S3"
            logger.error(f"Backup failed: {error_message}")
            event.fail(error_message)
            return

        disabled_connectivity = False
        if not self.charm.is_primary:
            # Create a rule to mark the cluster as in a creating backup state and update
            # the Patroni configuration.
            self._change_connectivity_to_database(connectivity=False)
            disabled_connectivity = True

        self.charm.unit.status = MaintenanceStatus("creating backup")
        # Set flag due to missing in progress backups on JSON output
        # (reference: https://github.com/pgbackrest/pgbackrest/issues/2007)
        self.charm.update_config(is_creating_backup=True)

        try:
            command = [
                "pgbackrest",
                f"--stanza={self.stanza_name}",
                "--log-level-console=debug",
                f"--type={BACKUP_TYPE_OVERRIDES[backup_type]}",
                "backup",
            ]
            if self.charm.is_primary:
                # Force the backup to run in the primary if it's not possible to run it
                # on the replicas (that happens when TLS is not enabled).
                command.append("--no-backup-standby")
            stdout, stderr = self._execute_command(command, stream=True)
            backup_id = list(self._list_backups(show_failed=True).keys())[-1]
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
                backup_id = self._generate_fake_backup_id(backup_type)

            # Upload the logs to S3.
            logs = f"""Stdout:
{e.stdout}

Stderr:
{e.stderr}
"""
            self._upload_content_to_s3(
                logs,
                f"backup/{self.stanza_name}/{backup_id}/backup.log",
                s3_parameters,
            )
            error_message = f"Failed to backup PostgreSQL with error: {e!s}"
            logger.error(f"Backup failed: {error_message}")
            event.fail(error_message)
        else:
            # Upload the logs to S3 and fail the action if it doesn't succeed.
            logs = f"""Stdout:
{stdout}

Stderr:
{stderr}
"""
            if not self._upload_content_to_s3(
                logs,
                f"backup/{self.stanza_name}/{backup_id}/backup.log",
                s3_parameters,
            ):
                error_message = "Error uploading logs to S3"
                logger.error(f"Backup failed: {error_message}")
                event.fail(error_message)
            else:
                logger.info(f"Backup succeeded: with backup-id {datetime_backup_requested}")
                event.set_results({"backup-status": "backup created"})

        if disabled_connectivity:
            # Remove the rule that marks the cluster as in a creating backup state
            # and update the Patroni configuration.
            self._change_connectivity_to_database(connectivity=True)

        self.charm.update_config(is_creating_backup=False)
        self.charm.unit.status = ActiveStatus()

    def _on_s3_credential_gone(self, _) -> None:
        self.container.stop(self.charm.rotate_logs_service)
        if self.charm.unit.is_leader():
            self.charm.app_peer_data.update({
                "stanza": "",
                "s3-initialization-start": "",
                "s3-initialization-done": "",
                "s3-initialization-block-message": "",
            })
        self.charm.unit_peer_data.update({
            "stanza": "",
            "s3-initialization-start": "",
            "s3-initialization-done": "",
            "s3-initialization-block-message": "",
        })
        if self.charm.is_blocked and self.charm.unit.status.message in S3_BLOCK_MESSAGES:
            self.charm._set_active_status()

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
            event.fail(f"Failed to list PostgreSQL backups with error: {e!s}")

    def _on_restore_action(self, event):  # noqa: C901
        """Request that pgBackRest restores a backup."""
        if not self._pre_restore_checks(event):
            return

        backup_id = event.params.get("backup-id")
        restore_to_time = event.params.get("restore-to-time")
        logger.info(
            f"A restore with backup-id {backup_id}"
            f"{f' to time point {restore_to_time}' if restore_to_time else ''}"
            f" has been requested on the unit"
        )

        # Validate the provided backup id and restore to time.
        logger.info("Validating provided backup-id and restore-to-time")
        backups = self._list_backups(show_failed=False)
        timelines = self._list_timelines()
        is_backup_id_real = backup_id and backup_id in backups
        is_backup_id_timeline = backup_id and not is_backup_id_real and backup_id in timelines
        if backup_id and not is_backup_id_real and not is_backup_id_timeline:
            error_message = f"Invalid backup-id: {backup_id}"
            logger.error(f"Restore failed: {error_message}")
            event.fail(error_message)
            return
        if is_backup_id_timeline and not restore_to_time:
            error_message = "Cannot restore to the timeline without restore-to-time parameter"
            logger.error(f"Restore failed: {error_message}")
            event.fail(error_message)
            return
        if is_backup_id_real:
            restore_stanza_timeline = backups[backup_id]
        elif is_backup_id_timeline:
            restore_stanza_timeline = timelines[backup_id]
        else:
            backups_list = list(self._list_backups(show_failed=False).values())
            timelines_list = self._list_timelines()
            if (
                restore_to_time == "latest"
                and timelines_list is not None
                and max(timelines_list.values() or [backups_list[0]]) not in backups_list
            ):
                error_message = "There is no base backup created from the latest timeline"
                logger.error(f"Restore failed: {error_message}")
                event.fail(error_message)
                return
            restore_stanza_timeline = self._get_nearest_timeline(restore_to_time)
            if not restore_stanza_timeline:
                error_message = f"Can't find the nearest timeline before timestamp {restore_to_time} to restore"
                logger.error(f"Restore failed: {error_message}")
                event.fail(error_message)
                return
            logger.info(
                f"Chosen timeline {restore_stanza_timeline[1]} as nearest for the specified timestamp {restore_to_time}"
            )

        self.charm.unit.status = MaintenanceStatus("restoring backup")

        # Temporarily disabling patroni (postgresql) pebble service auto-restart on failures. This is required
        # as point-in-time-recovery can fail on restore, therefore during cluster bootstrapping process. In this
        # case, we need be able to check patroni service status and logs. Disabling auto-restart feature is essential
        # to prevent wrong status indicated and logs reading race condition (as logs cleared / moved with service
        # restarts).
        if not self.charm.override_patroni_on_failure_condition("ignore", "restore-backup"):
            error_message = "Failed to override Patroni on-failure condition"
            logger.error(f"Restore failed: {error_message}")
            event.fail(error_message)
            return

        # Stop the database service before performing the restore.
        logger.info("Stopping database service")
        try:
            self.container.stop(self.charm._postgresql_service)
        except ChangeError as e:
            error_message = f"Failed to stop database service with error: {e!s}"
            logger.error(f"Restore failed: {error_message}")
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
            # If previous PITR restore was unsuccessful, there are no such endpoints.
            if not self.charm.is_cluster_restoring_to_time:
                error_message = f"Failed to remove previous cluster information with error: {e!s}"
                logger.error(f"Restore failed: {error_message}")
                event.fail(error_message)
                self._restart_database()
                return

        logger.info("Removing the contents of the data directory")
        try:
            self._empty_data_files()
        except ExecError as e:
            error_message = f"Failed to remove contents of the data directory with error: {e!s}"
            logger.error(f"Restore failed: {error_message}")
            event.fail(error_message)
            self._restart_database()
            return

        logger.info("Creating PostgreSQL data directory")
        self.charm._create_pgdata(self.container)

        # Mark the cluster as in a restoring backup state and update the Patroni configuration.
        logger.info("Configuring Patroni to restore the backup")
        self.charm.app_peer_data.update({
            "restoring-backup": self._fetch_backup_from_id(backup_id) if is_backup_id_real else "",
            "restore-stanza": restore_stanza_timeline[0],
            "restore-timeline": restore_stanza_timeline[1] if restore_to_time else "",
            "restore-to-time": restore_to_time or "",
            "s3-initialization-block-message": "",
        })
        self.charm.update_config()

        # Start the database to start the restore process.
        logger.info("Configuring Patroni to restore the backup")
        self.container.start(self.charm._postgresql_service)

        event.set_results({"restore-status": "restore started"})

    def _generate_fake_backup_id(self, backup_type: str) -> str:
        """Creates a backup id for failed backup operations (to store log file)."""
        if backup_type == "full":
            return datetime.strftime(datetime.now(), "%Y%m%d-%H%M%SF")
        if backup_type == "differential":
            backups = self._list_backups(show_failed=False, parse=False).keys()
            last_full_backup = None
            for label in backups[::-1]:
                if label.endswith("F"):
                    last_full_backup = label
                    break

            if last_full_backup is None:
                raise TypeError("Differential backup requested but no previous full backup")
            return f"{last_full_backup}_{datetime.strftime(datetime.now(), '%Y%m%d-%H%M%SD')}"
        if backup_type == "incremental":
            backups = self._list_backups(show_failed=False, parse=False).keys()
            if not backups:
                raise TypeError("Incremental backup requested but no previous successful backup")
            return f"{backups[-1]}_{datetime.strftime(datetime.now(), '%Y%m%d-%H%M%SI')}"

    def _fetch_backup_from_id(self, backup_id: str) -> str:
        """Fetches backup's pgbackrest label from backup id."""
        timestamp = f"{datetime.strftime(datetime.strptime(backup_id, '%Y-%m-%dT%H:%M:%SZ'), '%Y%m%d-%H%M%S')}"
        backups = self._list_backups(show_failed=False, parse=False).keys()
        for label in backups:
            if timestamp in label:
                return label

        return None

    def _pre_restore_checks(self, event: ActionEvent) -> bool:
        """Run some checks before starting the restore.

        Returns:
            a boolean indicating whether restore should be run.
        """
        are_backup_settings_ok, validation_message = self._are_backup_settings_ok()
        if not are_backup_settings_ok:
            logger.error(f"Restore failed: {validation_message}")
            event.fail(validation_message)
            return False

        if not event.params.get("backup-id") and event.params.get("restore-to-time") is None:
            error_message = "Either backup-id or restore-to-time parameters need to be provided to be able to do restore"
            logger.error(f"Restore failed: {error_message}")
            event.fail(error_message)
            return False

        # Quick check for timestamp format
        restore_to_time = event.params.get("restore-to-time")
        if (
            restore_to_time
            and restore_to_time != "latest"
            and not self._is_psql_timestamp(restore_to_time)
        ):
            error_message = "Bad restore-to-time format"
            logger.error(f"Restore failed: {error_message}")
            event.fail(error_message)
            return False

        if not self.container.can_connect():
            error_message = "Workload container not ready yet!"
            logger.error(f"Restore failed: {error_message}")
            event.fail(error_message)
            return False

        logger.info("Checking if cluster is in blocked state")
        if self.charm.is_blocked and self.charm.unit.status.message not in [
            ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE,
            CANNOT_RESTORE_PITR,
        ]:
            error_message = "Cluster or unit is in a blocking state"
            logger.error(f"Restore failed: {error_message}")
            event.fail(error_message)
            return False

        logger.info("Checking that the cluster does not have more than one unit")
        if self.charm.app.planned_units() > 1:
            error_message = (
                "Unit cannot restore backup as there are more than one unit in the cluster"
            )
            logger.error(f"Restore failed: {error_message}")
            event.fail(error_message)
            return False

        logger.info("Checking that the cluster is not replicating data to a standby cluster")
        for relation in [
            self.model.get_relation(REPLICATION_CONSUMER_RELATION),
            self.model.get_relation(REPLICATION_OFFER_RELATION),
        ]:
            if not relation:
                continue
            error_message = "Unit cannot restore backup as the cluster is replicating data to a standby cluster"
            logger.error(f"Restore failed: {error_message}")
            event.fail(error_message)
            return False

        logger.info("Checking that this unit was already elected the leader unit")
        if not self.charm.unit.is_leader():
            error_message = "Unit cannot restore backup as it was not elected the leader unit yet"
            logger.error(f"Restore failed: {error_message}")
            event.fail(error_message)
            return False

        return True

    def _render_pgbackrest_conf_file(self) -> bool:
        """Render the pgBackRest configuration file."""
        s3_parameters, missing_parameters = self._retrieve_s3_parameters()
        if missing_parameters:
            logger.warning(
                f"Cannot set pgBackRest configurations due to missing S3 parameters: {missing_parameters}"
            )
            return False

        if self._tls_ca_chain_filename != "":
            self.container.push(
                self._tls_ca_chain_filename,
                "\n".join(s3_parameters["tls-ca-chain"]),
                user=WORKLOAD_OS_USER,
                group=WORKLOAD_OS_GROUP,
            )

        # Open the template pgbackrest.conf file.
        with open("templates/pgbackrest.conf.j2") as file:
            template = Template(file.read())
        # Render the template file with the correct values.
        rendered = template.render(
            enable_tls=self.charm.is_tls_enabled and len(self.charm.peer_members_endpoints) > 0,
            peer_endpoints=self.charm.peer_members_endpoints,
            path=s3_parameters["path"],
            region=s3_parameters.get("region"),
            endpoint=s3_parameters["endpoint"],
            bucket=s3_parameters["bucket"],
            s3_uri_style=s3_parameters["s3-uri-style"],
            tls_ca_chain=self._tls_ca_chain_filename,
            access_key=s3_parameters["access-key"],
            secret_key=s3_parameters["secret-key"],
            stanza=self.stanza_name,
            storage_path=self.charm._storage_path,
            user=BACKUP_USER,
            retention_full=s3_parameters["delete-older-than-days"],
        )
        # Delete the original file and render the one with the right info.
        filename = "/etc/pgbackrest.conf"
        self.container.push(
            filename,
            rendered,
            user=WORKLOAD_OS_USER,
            group=WORKLOAD_OS_GROUP,
        )

        # Render the logrotate configuration file.
        with open("templates/pgbackrest.logrotate.j2") as file:
            template = Template(file.read())
        self.container.push(PGBACKREST_LOGROTATE_FILE, template.render())
        with open("src/rotate_logs.py") as f:
            self.container.push(
                "/home/postgres/rotate_logs.py",
                f.read(),
            )
        self.container.start(self.charm.rotate_logs_service)

        return True

    def _restart_database(self) -> None:
        """Removes the restoring backup flag and restart the database."""
        self.charm.app_peer_data.update({"restoring-backup": "", "restore-to-time": ""})
        self.charm.update_config()
        self.container.start(self.charm._postgresql_service)

    def _retrieve_s3_parameters(self) -> tuple[dict, list[str]]:
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
        s3_parameters.setdefault("delete-older-than-days", "9999999")

        # Strip whitespaces from all parameters.
        for key, value in s3_parameters.items():
            if isinstance(value, str):
                s3_parameters[key] = value.strip()

        # Clean up extra slash symbols to avoid issues on 3rd-party storages
        # like Ceph Object Gateway (radosgw).
        s3_parameters["endpoint"] = s3_parameters["endpoint"].rstrip("/")
        s3_parameters["path"] = (
            f"/{s3_parameters['path'].strip('/')}"  # The slash in the beginning is required by pgBackRest.
        )
        s3_parameters["bucket"] = s3_parameters["bucket"].strip("/")

        return s3_parameters, []

    def start_stop_pgbackrest_service(self) -> bool:
        """Start or stop the pgBackRest TLS server service.

        Returns:
            a boolean indicating whether the operation succeeded.
        """
        # Ignore this operation if backups settings aren't ok.
        are_backup_settings_ok, _ = self._are_backup_settings_ok()
        if not are_backup_settings_ok:
            return True

        # Update pgBackRest configuration (to update the TLS settings).
        if not self._render_pgbackrest_conf_file():
            return False

        # Stop the service if TLS is not enabled or there are no replicas.
        if (
            not self.charm.is_tls_enabled
            or len(self.charm.peer_members_endpoints) == 0
            or self.charm._patroni.get_standby_leader()
        ):
            self.container.stop(self.charm.pgbackrest_server_service)
            return True

        # Don't start the service if the service hasn't started yet in the primary.
        if not self.charm.is_primary and not self._is_primary_pgbackrest_service_running:
            return False

        # Start the service.
        self.container.restart(self.charm.pgbackrest_server_service)
        return True

    def _upload_content_to_s3(
        self,
        content: str,
        s3_path: str,
        s3_parameters: dict,
    ) -> bool:
        """Uploads the provided contents to the provided S3 bucket relative to the path from the S3 config.

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
        processed_s3_path = os.path.join(s3_parameters["path"], s3_path).lstrip("/")
        try:
            logger.info(f"Uploading content to bucket={bucket_name}, path={processed_s3_path}")
            session = boto3.session.Session(
                aws_access_key_id=s3_parameters["access-key"],
                aws_secret_access_key=s3_parameters["secret-key"],
                region_name=s3_parameters["region"],
            )

            s3 = session.resource(
                "s3",
                endpoint_url=self._construct_endpoint(s3_parameters),
                verify=(self._tls_ca_chain_filename or None),
            )
            bucket = s3.Bucket(bucket_name)

            with tempfile.NamedTemporaryFile() as temp_file:
                temp_file.write(content.encode("utf-8"))
                temp_file.flush()
                bucket.upload_file(temp_file.name, processed_s3_path)
        except Exception as e:
            logger.exception(
                f"Failed to upload content to S3 bucket={bucket_name}, path={processed_s3_path}",
                exc_info=e,
            )
            return False

        return True

    def _read_content_from_s3(self, s3_path: str, s3_parameters: dict) -> str | None:
        """Reads specified content from the provided S3 bucket relative to the path from the S3 config.

        Args:
            s3_path: The S3 path from which download the content
            s3_parameters: A dictionary containing the S3 parameters
                The following are expected keys in the dictionary: bucket, region,
                endpoint, access-key and secret-key

        Returns:
            a string with the content if object is successfully downloaded and None if file is not existing or error
            occurred during download.
        """
        bucket_name = s3_parameters["bucket"]
        processed_s3_path = os.path.join(s3_parameters["path"], s3_path).lstrip("/")
        try:
            logger.info(f"Reading content from bucket={bucket_name}, path={processed_s3_path}")
            session = boto3.session.Session(
                aws_access_key_id=s3_parameters["access-key"],
                aws_secret_access_key=s3_parameters["secret-key"],
                region_name=s3_parameters["region"],
            )
            s3 = session.resource(
                "s3",
                endpoint_url=self._construct_endpoint(s3_parameters),
                verify=(self._tls_ca_chain_filename or None),
            )
            bucket = s3.Bucket(bucket_name)
            with BytesIO() as buf:
                bucket.download_fileobj(processed_s3_path, buf)
                return buf.getvalue().decode("utf-8")
        except botocore.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "404":
                logger.info(
                    f"No such object to read from S3 bucket={bucket_name}, path={processed_s3_path}"
                )
            else:
                logger.exception(
                    f"Failed to read content from S3 bucket={bucket_name}, path={processed_s3_path}",
                    exc_info=e,
                )
        except Exception as e:
            logger.exception(
                f"Failed to read content from S3 bucket={bucket_name}, path={processed_s3_path}",
                exc_info=e,
            )

        return None

    def _s3_initialization_set_failure(
        self, block_message: str, update_leader_status: bool = True
    ):
        """Sets failed s3 initialization status with corresponding block_message in the app_peer_data (leader) or unit_peer_data (primary).

        Args:
            block_message: s3 initialization block message
            update_leader_status: whether to update leader status (with s3-initialization-block-message set already)
                immediately; defaults to True
        """
        if self.charm.unit.is_leader():
            # If performed on the leader, then leader == primary and there is no need to sync s3 data between two
            # different units. Therefore, there is no need for s3-initialization-done key, and it is sufficient to clear
            # s3-initialization-start key only.
            self.charm.app_peer_data.update({
                "s3-initialization-block-message": block_message,
                "s3-initialization-start": "",
                "stanza": "",
            })
            if update_leader_status:
                self.charm._set_active_status()
        else:
            self.charm.unit_peer_data.update({
                "s3-initialization-block-message": block_message,
                "s3-initialization-done": "True",
                "stanza": "",
            })
