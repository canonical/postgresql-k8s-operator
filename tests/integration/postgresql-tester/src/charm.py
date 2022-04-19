#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""A charm to test PostgreSQL operator and its charm library."""

import logging

import psycopg2
from charms.postgresql.v0.postgresql import (
    DatabaseAvailableEvent,
    PostgreSQLEvents,
    PostgreSQLRequires,
    ProxyAuthDetailsAvailableEvent,
)
from ops.charm import CharmBase, RelationJoinedEvent
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from psycopg2.extensions import parse_dsn

logger = logging.getLogger(__name__)

DATABASE_RELATION = "database"
PROXY_RELATION = "proxy"
DATABASE_NAME = "postgresql_tester"
PROXY_DATABASE = "proxy"


class PostgreSQLTesterCharm(CharmBase):
    """A charm to test PostgreSQL operator and its charm library."""

    on = PostgreSQLEvents()

    def __init__(self, *args):
        super().__init__(*args)

        # Charm library helper class for consumer and proxy applications.
        self.postgresql_requires = PostgreSQLRequires(self, DATABASE_RELATION, PROXY_RELATION)

        self.framework.observe(self.on.start, self._on_start)

        # Database relation events.
        self.framework.observe(self.on.database_relation_joined, self._on_database_relation_joined)
        self.framework.observe(
            self.on.database_available, self._on_database_available
        )  # Custom event defined in the library.

        # Proxy relation events.
        self.framework.observe(self.on.proxy_relation_joined, self._on_proxy_relation_joined)
        self.framework.observe(
            self.on.proxy_auth_details_available, self._on_proxy_auth_details_available
        )  # Custom event defined in the library.

    def _on_start(self, _) -> None:
        self.unit.status = ActiveStatus("unit started")

    def _on_database_relation_joined(self, event: RelationJoinedEvent) -> None:
        # Only the leader is allowed to request for a database,
        # so we don't ask twice or more times.
        if not self.unit.is_leader():
            return

        # Request a database and a user creation on PostgreSQL.
        self.postgresql_requires.set_database(event.relation, DATABASE_NAME)
        self.unit.status = WaitingStatus("awaiting database connection details")

    def _on_database_available(self, event: DatabaseAvailableEvent) -> None:
        # This event is triggered after PostgreSQL creates the user
        # and the database that were requested.
        # Get the connection strings for the primary instance and the replicas.
        endpoints = event.endpoints
        read_only_endpoints = event.read_only_endpoints.split(",")

        # Build a list with all the connection strings.
        connection_strings = [endpoints]
        connection_strings.extend(read_only_endpoints)

        # Test each connection string.
        for connection_string in connection_strings:
            with psycopg2.connect(connection_string) as connection:
                if connection.status != psycopg2.extensions.STATUS_READY:
                    self.unit.status = BlockedStatus("could not connect to database")
                    return

        self.unit.status = ActiveStatus("database connected")

    def _on_proxy_relation_joined(self, event: RelationJoinedEvent) -> None:
        # Only the leader is allowed to request proxy configuration,
        # so we don't ask twice or more times.
        if not self.unit.is_leader():
            return

        # Ask for configuration of Proxy on PostgreSQL side (create user, database
        # and auth_query function - the function that we can use to match users/passwords)
        self.postgresql_requires.set_database(event.relation, PROXY_DATABASE)
        self.unit.status = WaitingStatus("awaiting proxy authentication details")

    def _on_proxy_auth_details_available(self, event: ProxyAuthDetailsAvailableEvent) -> None:
        # Get information about user, password and auth_query function created
        # and which are need for PgBouncer to connect do PostgreSQL and match
        # users/passwords from the clients charm connecting to PgBouncer.
        auth_user = event.proxy_auth_user
        auth_password = event.proxy_auth_password
        auth_query = event.proxy_auth_query
        # Get the primary host from the connection string.
        primary_host = parse_dsn(event.endpoints)["host"]

        if auth_user and auth_password and auth_query:
            # Connect to the primary instance using the proxy auth details.
            with psycopg2.connect(
                f"postgresql://{auth_user}:{auth_password}@{primary_host}:5432/{PROXY_DATABASE}"
            ) as connection, connection.cursor() as cursor:
                # Test the auth_query function.
                cursor.execute(f"SELECT * FROM {auth_query}('{auth_user}')")
                if cursor.fetchone():
                    self.unit.status = ActiveStatus("proxy connected")
                else:
                    self.unit.status = BlockedStatus("could not use auth_query")


if __name__ == "__main__":
    main(PostgreSQLTesterCharm)
