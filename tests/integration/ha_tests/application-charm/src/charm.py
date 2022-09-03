#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Application charm that connects to database charms.

This charm is meant to be used only for testing
high availability of the PostgreSQL charm.
"""

import logging
import subprocess
from typing import Optional

import psycopg2
from charms.data_platform_libs.v0.database_requires import (
    DatabaseCreatedEvent,
    DatabaseEndpointsChangedEvent,
    DatabaseRequires,
)
from ops.charm import ActionEvent, CharmBase
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus

logger = logging.getLogger(__name__)


class ApplicationCharm(CharmBase):
    """Application charm that connects to PostgreSQL charm."""

    _stored = StoredState()

    def __init__(self, *args):
        super().__init__(*args)

        # Default charm events.
        self.framework.observe(self.on.start, self._on_start)

        # Events related to the database that is requested.
        self.database_name = "application"
        self.database = DatabaseRequires(self, "database", self.database_name)
        self.framework.observe(self.database.on.database_created, self._on_database_created)
        self.framework.observe(self.database.on.endpoints_changed, self._on_endpoints_changed)
        self.framework.observe(
            self.on.stop_continuous_writes_action, self._on_stop_continuous_writes_action
        )

        self._stored.set_default(continuous_writes_pid=0)

    @property
    def _connection_string(self) -> Optional[str]:
        data = list(self.database.fetch_relation_data().values())[0]
        logger.warning(f"data: {data}")
        username = data.get("username")
        password = data.get("password")
        endpoints = data.get("endpoints")
        logger.warning([username, password, endpoints])
        if None in [username, password, endpoints]:
            return None

        host = endpoints.split(":")[0]
        return (
            f"dbname='{self.database_name}' user='{username}'"
            f" host='{host}' password='{password}' connect_timeout=10"
        )

    def _on_start(self, _) -> None:
        """Only sets an Active status."""
        self.unit.status = ActiveStatus()

    def _on_database_created(self, event: DatabaseCreatedEvent) -> None:
        """Event triggered when a database was created for this application."""
        # Retrieve the credentials using the charm library.
        print(f"database credentials: {event.username} {event.password}")
        # pass arguments (including endpoints and database name) to continuous
        # writes service and start the service.
        self._start_continuous_writes(1)

    def _on_endpoints_changed(self, event: DatabaseEndpointsChangedEvent) -> None:
        """Event triggered when the read/write endpoints of the database change."""
        print(event.endpoints)
        # pass new endpoints and reload (SIGHUP) continuous writes service.
        count = self._count_writes()
        self._start_continuous_writes(count + 1)

    def _count_writes(self) -> int:
        """Count the number of records in the continuous_writes table."""
        with psycopg2.connect(
            self._connection_string
        ) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(number) FROM continuous_writes;")
            count = cursor.fetchone()[0]
        connection.close()
        print(f"count: {count}")
        return count

    def _on_stop_continuous_writes_action(self, event: ActionEvent) -> None:
        writes = self._stop_continuous_writes()
        event.set_results({"writes": writes})

    def _start_continuous_writes(self, starting_number: int) -> None:
        """Starts continuous writes to PostgreSQL instance."""
        if self._connection_string is None:
            return

        self._stop_continuous_writes()

        # run continuous writes in the background.
        logger.warning(self._connection_string)
        self._stored.continuous_writes_pid = subprocess.Popen(
            [
                "/usr/bin/python3",
                "src/continuous_writes.py",
                self._connection_string,
                str(starting_number),
            ]
        ).pid

    def _stop_continuous_writes(self) -> Optional[int]:
        """Stops continuous writes to PostgreSQL and returns the last written value."""
        if not self._stored.continuous_writes_pid:
            return None

        # stop the process
        proc = subprocess.Popen(["pkill", "-9", "-f", "continuous_writes.py"])

        # wait for process to be killed
        proc.communicate()

        self._stored.continuous_writes_pid = 0

        with psycopg2.connect(
            self._connection_string
        ) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT MAX(number) FROM continuous_writes;")
            last_written_value = cursor.fetchone()[0]
        connection.close()
        print(f"last_written_value: {last_written_value}")
        return last_written_value


if __name__ == "__main__":
    main(ApplicationCharm)
