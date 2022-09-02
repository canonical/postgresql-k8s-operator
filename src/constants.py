# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""File containing constants to be used in the charm."""

DATABASE_PORT = "5432"
PEER = "database-peers"
REPLICATION_PASSWORD_KEY = "replication-password"
USER = "operator"
USER_PASSWORD_KEY = "operator-password"
WORKLOAD_OS_USER_GROUP = "postgres"
TLS_EXT_PEM_FILE = "external-cert.pem"
TLS_EXT_CA_FILE = "external-ca.crt"
TLS_INT_PEM_FILE = "internal-cert.pem"
TLS_INT_CA_FILE = "internal-ca.crt"
