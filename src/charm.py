#!/usr/bin/env python3
# Copyright 2021 Canonical
# See LICENSE file for licensing details.

import logging
import secrets
import string

from ops.charm import CharmBase
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus, WaitingStatus
from ops.pebble import Layer

logger = logging.getLogger(__name__)


class PostgresqlOperatorCharm(CharmBase):
    """Charmed Operator for the PostgreSQL database."""

    _stored = StoredState()

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.config_changed, self._on_config_changed)

    def _on_install(self, _):
        """Event handler for InstallEvent."""
        # TODO: change to peer/leader data bag when relations are implemented.
        self._stored.postgres_password = self._new_password()

    def _on_config_changed(self, _):
        """Handle the config-changed event"""
        # Get the postgresql container so we can configure/manipulate it
        container = self.unit.get_container("postgresql")
        # Create a new config layer
        layer = self._postgresql_layer()

        if container.can_connect():
            # Get the current config
            services = container.get_plan().services
            # Check if there are any changes to services
            if services != layer.services:
                # Changes were made, add the new layer
                container.add_layer("postgresql", layer, combine=True)
                logging.info("Added updated layer 'postgresql' to Pebble plan")
                # Restart it and report a new status to Juju
                container.restart("postgresql")
                logging.info("Restarted postgresql service")
            # All is well, set an ActiveStatus
            self.unit.status = ActiveStatus()
        else:
            self.unit.status = WaitingStatus("waiting for Pebble in workload container")

    def _postgresql_layer(self) -> Layer:
        """Returns a Pebble configuration layer for PostgreSQL"""
        layer_config = {
            "summary": "postgresql layer",
            "description": "pebble config layer for postgresql",
            "services": {
                "postgresql": {
                    "override": "replace",
                    "summary": "entrypoint of the postgresql image",
                    "command": "/usr/local/bin/docker-entrypoint.sh postgres",
                    "startup": "enabled",
                    "environment": {
                        "PGDATA": "/var/lib/postgresql/data/pgdata",
                        # We need to set either POSTGRES_HOST_AUTH_METHOD or POSTGRES_PASSWORD
                        # in order to initialize the database.
                        "POSTGRES_PASSWORD": self._stored.postgres_password,
                    },
                }
            },
        }
        return Layer(layer_config)

    def _new_password(self):
        """Generate a random password string.

        Returns:
           A random password string.
        """
        choices = string.ascii_letters + string.digits
        password = "".join([secrets.choice(choices) for i in range(16)])
        return password


if __name__ == "__main__":
    main(PostgresqlOperatorCharm, use_juju_for_storage=True)
