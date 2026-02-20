# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""File containing constants to be used in the charm."""

DATABASE_DEFAULT_NAME = "postgres"
DATABASE_PORT = "5432"
PEER = "database-peers"
API_REQUEST_TIMEOUT = 5
PATRONI_CLUSTER_STATUS_ENDPOINT = "cluster"
BACKUP_USER = "backup"
REPLICATION_USER = "replication"
REWIND_USER = "rewind"
MONITORING_USER = "monitoring"
TLS_KEY_FILE = "key.pem"
TLS_CA_FILE = "ca.pem"
TLS_CERT_FILE = "cert.pem"
USER = "operator"
WORKLOAD_OS_GROUP = "postgres"
WORKLOAD_OS_USER = "postgres"
METRICS_PORT = "9187"
PGBACKREST_METRICS_PORT = "9854"
POSTGRESQL_DATA_PATH = "/var/lib/postgresql/data/pgdata"
POSTGRESQL_LOGS_PATH = "/var/log/postgresql"
POSTGRESQL_LOGS_PATTERN = "postgresql*.log"
POSTGRES_LOG_FILES = [
    "/var/log/pgbackrest/*",
    "/var/log/postgresql/patroni.log",
    "/var/log/postgresql/postgresql*.log",
]
# List of system usernames needed for correct work of the charm/workload.
SYSTEM_USERS = [BACKUP_USER, REPLICATION_USER, REWIND_USER, USER, MONITORING_USER]

# Labels are not confidential
REPLICATION_PASSWORD_KEY = "replication-password"  # noqa: S105
REWIND_PASSWORD_KEY = "rewind-password"  # noqa: S105
MONITORING_PASSWORD_KEY = "monitoring-password"  # noqa: S105
PATRONI_PASSWORD_KEY = "patroni-password"  # noqa: S105
USER_PASSWORD_KEY = "operator-password"  # noqa: S105
SECRET_LABEL = "secret"  # noqa: S105
SECRET_CACHE_LABEL = "cache"  # noqa: S105
SECRET_INTERNAL_LABEL = "internal-secret"  # noqa: S105
SECRET_DELETED_LABEL = "None"  # noqa: S105

APP_SCOPE = "app"
UNIT_SCOPE = "unit"

SECRET_KEY_OVERRIDES = {"ca": "cauth"}
BACKUP_TYPE_OVERRIDES = {"full": "full", "differential": "diff", "incremental": "incr"}
PLUGIN_OVERRIDES = {"audit": "pgaudit", "uuid_ossp": '"uuid-ossp"'}

SPI_MODULE = ["refint", "autoinc", "insert_username", "moddatetime"]

TRACING_RELATION_NAME = "tracing"

DATABASE = "database"
LEGACY_DB = "db"
LEGACY_DB_ADMIN = "db-admin"
ALL_LEGACY_RELATIONS = [LEGACY_DB, LEGACY_DB_ADMIN]

ENDPOINT_SIMULTANEOUSLY_BLOCKING_MESSAGE = (
    "Please choose one endpoint to use. No need to relate all of them simultaneously!"
)

PGBACKREST_LOGROTATE_FILE = "/etc/logrotate.d/pgbackrest.logrotate"
