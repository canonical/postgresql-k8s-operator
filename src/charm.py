#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed Kubernetes Operator for the PostgreSQL database."""

import logging
import secrets
import string

from ops.charm import ActionEvent, CharmBase, WorkloadEvent
from ops.main import main
from ops.model import ActiveStatus, Relation, WaitingStatus
from ops.pebble import Layer

logger = logging.getLogger(__name__)


class PostgresqlOperatorCharm(CharmBase):
    """Charmed Operator for the PostgreSQL database."""

    def __init__(self, *args):
        super().__init__(*args)

        self._postgresql_service = "postgresql"

        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.leader_elected, self._on_leader_elected)
        self.framework.observe(self.on.postgresql_pebble_ready, self._on_postgresql_pebble_ready)
        self.framework.observe(
            self.on.get_postgres_password_action, self._on_get_postgres_password
        )

    def _on_install(self, _):
        """Event handler for InstallEvent."""
        # TODO: placeholder method to implement logic specific to install event.
        pass

    def _on_config_changed(self, _):
        """Handle the config-changed event."""
        # TODO: placeholder method to implement logic specific to configuration change.
        pass

    def _on_leader_elected(self, _) -> None:
        """Handle the leader-elected event."""
        data = self._peers.data[self.app]
        postgres_password = data.get("postgres-password", None)

        if postgres_password is None:
            self._peers.data[self.app]["postgres-password"] = self._new_password()

    def _on_postgresql_pebble_ready(self, event: WorkloadEvent) -> None:
        """Event handler for on PebbleReadyEvent."""
        # TODO: move this code to an "_update_layer" method in order to also utilize it in
        # config-changed hook.
        # Get the postgresql container so we can configure/manipulate it.
        container = event.workload
        # Create a new config layer.
        new_layer = self._postgresql_layer()

        if container.can_connect():
            # Get the current layer.
            current_layer = container.get_plan()
            # Check if there are any changes to layer services.
            if current_layer.services != new_layer.services:
                # Changes were made, add the new layer.
                container.add_layer(self._postgresql_service, new_layer, combine=True)
                logging.info("Added updated layer 'postgresql' to Pebble plan")
                # Restart it and report a new status to Juju.
                container.restart(self._postgresql_service)
                logging.info("Restarted postgresql service")
            # All is well, set an ActiveStatus.
            self.unit.status = ActiveStatus()
        else:
            self.unit.status = WaitingStatus("waiting for Pebble in workload container")

    def _on_get_postgres_password(self, event: ActionEvent) -> None:
        """Returns the password for the postgres user as an action response."""
        event.set_results({"postgres-password": self._get_postgres_password()})

    def _postgresql_layer(self) -> Layer:
        """Returns a Pebble configuration layer for PostgreSQL."""
        layer_config = {
            "summary": "postgresql layer",
            "description": "pebble config layer for postgresql",
            "services": {
                self._postgresql_service: {
                    "override": "replace",
                    "summary": "entrypoint of the postgresql image",
                    "command": "/usr/local/bin/docker-entrypoint.sh postgres",
                    "startup": "enabled",
                    "environment": {
                        "PGDATA": "/var/lib/postgresql/data/pgdata",
                        # We need to set either POSTGRES_HOST_AUTH_METHOD or POSTGRES_PASSWORD
                        # in order to initialize the database.
                        "POSTGRES_PASSWORD": self._get_postgres_password(),
                    },
                }
            },
        }
        return Layer(layer_config)

    def _new_password(self) -> str:
        """Generate a random password string.

        Returns:
           A random password string.
        """
        choices = string.ascii_letters + string.digits
        password = "".join([secrets.choice(choices) for i in range(16)])
        return password

    @property
    def _peers(self) -> Relation:
        """Fetch the peer relation.

        Returns:
             A :class:`ops.model.Relation` object representing
             the peer relation.
        """
        return self.model.get_relation("postgresql-replicas")

    def _get_postgres_password(self) -> str:
        """Get postgres user password."""
        data = self._peers.data[self.app]
        return data.get("postgres-password", None)


if __name__ == "__main__":
    main(PostgresqlOperatorCharm, use_juju_for_storage=True)
