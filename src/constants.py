# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""File containing constants to be used in the charm."""

from single_kernel_postgresql.config.literals import BACKUP_USER

REPLICATION_USER = "replication"
REWIND_USER = "rewind"
MONITORING_USER = "monitoring"
TLS_CA_BUNDLE_FILE = "peer_ca_bundle.pem"
USER = "operator"
WORKLOAD_OS_GROUP = "postgres"
WORKLOAD_OS_USER = "postgres"
PATRONI_LOGS_SYMLINK_PATH = "/var/log/patroni"
PGBACKREST_LOGS_SYMLINK_PATH = "/var/log/pgbackrest"
POSTGRESQL_LOGS_SYMLINK_PATH = "/var/log/postgresql"

# Storage mount paths (must match metadata.yaml storage locations).
STORAGE_PATH = "/var/lib/pg"
ARCHIVE_PATH = f"{STORAGE_PATH}/archive"
DATA_STORAGE_PATH = f"{STORAGE_PATH}/data"
LOGS_STORAGE_PATH = f"{STORAGE_PATH}/logs"
TEMP_STORAGE_PATH = f"{STORAGE_PATH}/temp"
POSTGRESQL_LOGS_PATH = f"{LOGS_STORAGE_PATH}/16/main/pg_logs"
PATRONI_LOGS_PATH = f"{LOGS_STORAGE_PATH}/16/main/patroni_logs"
PGBACKREST_LOGS_PATH = f"{LOGS_STORAGE_PATH}/16/main/pgbackrest_logs"
POSTGRESQL_LOGS_PATTERN = "postgresql*.log"
POSTGRES_LOG_FILES = [
    f"{PGBACKREST_LOGS_PATH}/*",
    f"{PATRONI_LOGS_PATH}/patroni.log",
    f"{POSTGRESQL_LOGS_PATH}/postgresql*.log",
]
# List of system usernames needed for correct work of the charm/workload.
SYSTEM_USERS = [BACKUP_USER, REPLICATION_USER, REWIND_USER, USER, MONITORING_USER]

# Labels are not confidential
SECRET_LABEL = "secret"  # noqa: S105
SECRET_CACHE_LABEL = "cache"  # noqa: S105
