# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""File containing constants to be used in the charm."""

from single_kernel_postgresql.config.literals import (  # noqa: F401
    APP_SCOPE,
    BACKUP_TYPE_OVERRIDES,
    BACKUP_USER,
    DATABASE,
    DATABASE_DEFAULT_NAME,
    DATABASE_MAPPING_LABEL,
    DATABASE_PORT,
    METRICS_PORT,
    MONITORING_PASSWORD_KEY,
    PATRONI_CLUSTER_STATUS_ENDPOINT,
    PATRONI_PASSWORD_KEY,
    PEER_RELATION,
    PGBACKREST_LOGROTATE_FILE,
    PGBACKREST_METRICS_PORT,
    PLUGIN_OVERRIDES,
    REPLICATION_PASSWORD_KEY,
    REWIND_PASSWORD_KEY,
    SECRET_DELETED_LABEL,
    SECRET_INTERNAL_LABEL,
    SECRET_KEY_OVERRIDES,
    SPI_MODULE,
    SYSTEM_USERS_PASSWORD_CONFIG,
    TLS_CA_FILE,
    TLS_CERT_FILE,
    TLS_KEY_FILE,
    TRACING_RELATION_NAME,
    UNIT_SCOPE,
    USER_PASSWORD_KEY,
    USERNAME_MAPPING_LABEL,
)

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
