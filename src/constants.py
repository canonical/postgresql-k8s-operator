# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""File containing constants to be used in the charm."""

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
