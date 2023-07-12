# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""File containing constants to be used in the charm."""

DATABASE_PORT = "5432"
PEER = "database-peers"
BACKUP_USER = "backup"
REPLICATION_USER = "replication"
REPLICATION_PASSWORD_KEY = "replication-password"
REWIND_USER = "rewind"
REWIND_PASSWORD_KEY = "rewind-password"
MONITORING_USER = "monitoring"
MONITORING_PASSWORD_KEY = "monitoring-password"
TLS_KEY_FILE = "key.pem"
TLS_CA_FILE = "ca.pem"
TLS_CERT_FILE = "cert.pem"
USER = "operator"
USER_PASSWORD_KEY = "operator-password"
WORKLOAD_OS_GROUP = "postgres"
WORKLOAD_OS_USER = "postgres"
METRICS_PORT = "9187"
POSTGRES_LOG_FILES = [
    "/var/log/pgbackrest",
    "/var/log/postgresql/patroni.log",
    "/var/log/postgresql/postgresql.log",
]
# List of system usernames needed for correct work of the charm/workload.
SYSTEM_USERS = [BACKUP_USER, REPLICATION_USER, REWIND_USER, USER, MONITORING_USER]

DEPS = {
    "charm": {
        "dependencies": {"pgbouncer": ">0"},
        "name": "postgresql",
        "upgrade_supported": ">0",
        "version": "1",
    }
}
