#!/usr/bin/env python3
# Copyright 2021 Canonical
# See LICENSE file for licensing details.

import logging

from ops.charm import CharmBase
from ops.main import main
from ops.model import ActiveStatus, WaitingStatus

logger = logging.getLogger(__name__)


class PostgresqlOperatorCharm(CharmBase):
    """Charmed Operator for the PostgreSQL database."""

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.config_changed, self._on_config_changed)

    def _on_config_changed(self, _):
        """Handle the config-changed event"""
        # Get the postgresql container so we can configure/manipulate it
        container = self.unit.get_container("postgresql")
        # Create a new config layer
        layer = self._postgresql_layer()

        if container.can_connect():
            # Get the current config
            services = container.get_plan().to_dict().get("services", {})
            # Check if there are any changes to services
            if services != layer["services"]:
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

    def _postgresql_layer(self):
        """Returns a Pebble configuration layer for PostgreSQL"""
        return {
            "summary": "postgresql layer",
            "description": "pebble config layer for postgresql",
            "services": {
                "postgresql": {
                    "override": "replace",
                    "summary": "entrypoint of the postgresql image",
                    "command": "/usr/local/bin/docker-entrypoint.sh postgres",
                    "startup": "enabled",
                    "environment": {
                        # We need to set either POSTGRES_HOST_AUTH_METHOD or POSTGRES_PASSWORD
                        # in order to initialize the database.
                        # Currently, this password can only be set on deploy.
                        "POSTGRES_PASSWORD": self.config["postgres-password"]
                    }
                }
            },
        }


if __name__ == "__main__":
    main(PostgresqlOperatorCharm)
