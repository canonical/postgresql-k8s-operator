# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Postgres client relation hooks & helpers."""

import logging

from charms.data_platform_libs.v0.data_interfaces import (
    DatabaseProvides,
    DatabaseRequestedEvent,
)
from charms.postgresql_k8s.v0.postgresql import (
    INVALID_EXTRA_USER_ROLE_BLOCKING_MESSAGE,
    PostgreSQLCreateDatabaseError,
    PostgreSQLCreateUserError,
    PostgreSQLDeleteUserError,
    PostgreSQLGetPostgreSQLVersionError,
)
from ops.charm import CharmBase, RelationBrokenEvent, RelationDepartedEvent
from ops.framework import Object
from ops.model import ActiveStatus, BlockedStatus, Relation

from constants import DATABASE_PORT
from utils import new_password

logger = logging.getLogger(__name__)


class PostgreSQLProvider(Object):
    """Defines functionality for the 'provides' side of the 'postgresql-client' relation.

    Hook events observed:
        - database-requested
        - relation-broken
    """

    def __init__(self, charm: CharmBase, relation_name: str = "database") -> None:
        """Constructor for PostgreSQLClientProvides object.

        Args:
            charm: the charm for which this relation is provided
            relation_name: the name of the relation
        """
        self.relation_name = relation_name

        super().__init__(charm, self.relation_name)
        self.framework.observe(
            charm.on[self.relation_name].relation_departed, self._on_relation_departed
        )
        self.framework.observe(
            charm.on[self.relation_name].relation_broken, self._on_relation_broken
        )

        self.charm = charm

        # Charm events defined in the database provides charm library.
        self.database_provides = DatabaseProvides(self.charm, relation_name=self.relation_name)
        self.framework.observe(
            self.database_provides.on.database_requested, self._on_database_requested
        )

    def _on_database_requested(self, event: DatabaseRequestedEvent) -> None:
        """Handle the legacy postgresql-client relation changed event.

        Generate password and handle user and database creation for the related application.
        """
        # Check for some conditions before trying to access the PostgreSQL instance.
        if (
            "cluster_initialised" not in self.charm._peers.data[self.charm.app]
            or not self.charm._patroni.member_started
        ):
            logger.debug(
                "Deferring on_database_requested: Cluster must be initialized before database can be requested"
            )
            event.defer()
            return

        if not self.charm.unit.is_leader():
            return

        # Retrieve the database name and extra user roles using the charm library.
        database = event.database
        extra_user_roles = event.extra_user_roles

        try:
            # Creates the user and the database for this specific relation.
            user = f"relation_id_{event.relation.id}"
            password = new_password()
            self.charm.postgresql.create_user(user, password, extra_user_roles=extra_user_roles)
            plugins = [
                "_".join(plugin.split("_")[1:-1])
                for plugin in self.charm.config.plugin_keys()
                if self.charm.config[plugin]
            ]

            self.charm.postgresql.create_database(
                database, user, plugins=plugins, client_relations=self.charm.client_relations
            )

            # Share the credentials with the application.
            self.database_provides.set_credentials(event.relation.id, user, password)

            # Set the read/write endpoint.
            self.database_provides.set_endpoints(
                event.relation.id,
                f"{self.charm.primary_endpoint}:{DATABASE_PORT}",
            )

            # Update the read-only endpoint.
            self.update_read_only_endpoint(event)

            # Set the database version.
            self.database_provides.set_version(
                event.relation.id, self.charm.postgresql.get_postgresql_version()
            )

            # Set the database name
            self.database_provides.set_database(event.relation.id, database)

            self._update_unit_status(event.relation)
        except (
            PostgreSQLCreateDatabaseError,
            PostgreSQLCreateUserError,
            PostgreSQLGetPostgreSQLVersionError,
        ) as e:
            logger.exception(e)
            self.charm.unit.status = BlockedStatus(
                e.message
                if issubclass(type(e), PostgreSQLCreateUserError) and e.message is not None
                else f"Failed to initialize {self.relation_name} relation"
            )

    def _on_relation_departed(self, event: RelationDepartedEvent) -> None:
        """Set a flag to avoid deleting database users when not wanted."""
        # Set a flag to avoid deleting database users when this unit
        # is removed and receives relation broken events from related applications.
        # This is needed because of https://bugs.launchpad.net/juju/+bug/1979811.
        if event.departing_unit == self.charm.unit:
            self.charm._peers.data[self.charm.unit].update({"departing": "True"})

    def _on_relation_broken(self, event: RelationBrokenEvent) -> None:
        """Remove the user created for this relation."""
        # Check for some conditions before trying to access the PostgreSQL instance.
        if (
            not self.charm._peers
            or "cluster_initialised" not in self.charm._peers.data[self.charm.app]
            or not self.charm._patroni.member_started
        ):
            logger.debug(
                "Deferring on_relation_broken: Cluster must be initialized before user can be deleted"
            )
            event.defer()
            return

        self._update_unit_status(event.relation)

        if "departing" in self.charm._peers.data[self.charm.unit]:
            logger.debug("Early exit on_relation_broken: Skipping departing unit")
            return

        if not self.charm.unit.is_leader():
            return

        # Delete the user.
        user = f"relation_id_{event.relation.id}"
        try:
            self.charm.postgresql.delete_user(user)
        except PostgreSQLDeleteUserError as e:
            logger.exception(e)
            self.charm.unit.status = BlockedStatus(
                f"Failed to delete user during {self.relation_name} relation broken event"
            )

    def update_read_only_endpoint(self, event: DatabaseRequestedEvent = None) -> None:
        """Set the read-only endpoint only if there are replicas."""
        if not self.charm.unit.is_leader():
            return

        # If there are no replicas, remove the read-only endpoint.
        endpoints = (
            f"{self.charm.replicas_endpoint}:{DATABASE_PORT}"
            if len(self.charm._peers.units) > 0
            else ""
        )

        # Get the current relation or all the relations
        # if this is triggered by another type of event.
        relations = [event.relation] if event else self.model.relations[self.relation_name]

        for relation in relations:
            self.database_provides.set_read_only_endpoints(
                relation.id,
                endpoints,
            )

    def _update_unit_status(self, relation: Relation) -> None:
        """# Clean up Blocked status if it's due to extensions request."""
        if (
            self.charm._has_blocked_status
            and self.charm.unit.status.message == INVALID_EXTRA_USER_ROLE_BLOCKING_MESSAGE
        ):
            if not self.check_for_invalid_extra_user_roles(relation.id):
                self.charm.unit.status = ActiveStatus()

    def check_for_invalid_extra_user_roles(self, relation_id: int) -> bool:
        """Checks if there are relations with invalid extra user roles.

        Args:
            relation_id: current relation to be skipped.
        """
        valid_privileges, valid_roles = self.charm.postgresql.list_valid_privileges_and_roles()
        for relation in self.charm.model.relations.get(self.relation_name, []):
            if relation.id == relation_id:
                continue
            for data in relation.data.values():
                extra_user_roles = data.get("extra-user-roles")
                if extra_user_roles is None:
                    break
                extra_user_roles = extra_user_roles.lower().split(",")
                for extra_user_role in extra_user_roles:
                    if (
                        extra_user_role not in valid_privileges
                        and extra_user_role not in valid_roles
                    ):
                        return True
        return False
