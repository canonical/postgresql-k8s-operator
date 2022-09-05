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
TLS_EXT_KEY_FILE = "external-key.pem"
TLS_EXT_CA_FILE = "external-ca.pem"
TLS_EXT_CERT_FILE = "external-cert.pem"
TLS_INT_KEY_FILE = "internal-key.pem"
TLS_INT_CA_FILE = "internal-ca.pem"
TLS_INT_CERT_FILE = "internal-cert.pem"
# List of system usernames needed for correct work of the charm/workload.
SYSTEM_USERS = [REPLICATION_USER, USER]
