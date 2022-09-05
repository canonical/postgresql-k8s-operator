# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""File containing constants to be used in the charm."""

DATABASE_PORT = "5432"
PEER = "database-peers"
REPLICATION_USER = "replication"
REPLICATION_PASSWORD_KEY = "replication-password"
USER = "operator"
USER_PASSWORD_KEY = "operator-password"
WORKLOAD_OS_GROUP = "postgres"
WORKLOAD_OS_USER = "postgres"
TLS_EXT_KEY_FILE = "external-key.key"
TLS_EXT_CA_FILE = "external-ca.crt"
TLS_EXT_CERT_FILE = "external-cert.crt"
TLS_INT_KEY_FILE = "internal-key.key"
TLS_INT_CA_FILE = "internal-ca.crt"
TLS_INT_CERT_FILE = "internal-cert.crt"
# List of system usernames needed for correct work of the charm/workload.
SYSTEM_USERS = [REPLICATION_USER, USER]
