# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""File containing constants to be used in the charm."""

BACKUP_USER = "backup"
BACKUP_PASSWORD_KEY = "backup-password"
DATABASE_PORT = "5432"
PEER = "database-peers"
REPLICATION_USER = "replication"
REPLICATION_PASSWORD_KEY = "replication-password"
TLS_KEY_FILE = "key.pem"
TLS_CA_FILE = "ca.pem"
TLS_CERT_FILE = "cert.pem"
USER = "operator"
USER_PASSWORD_KEY = "operator-password"
WORKLOAD_OS_GROUP = "postgres"
WORKLOAD_OS_USER = "postgres"
# List of system usernames needed for correct work of the charm/workload.
SYSTEM_USERS = [BACKUP_USER, REPLICATION_USER, USER]
