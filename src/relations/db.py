# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Postgres db and db-admin relation hooks & helpers."""

import logging
from typing import Iterable, List, Set, Tuple

from charms.postgresql_k8s.v0.postgresql import (
    PostgreSQLCreateDatabaseError,
    PostgreSQLCreateUserError,
    PostgreSQLDeleteUserError,
    PostgreSQLGetPostgreSQLVersionError,
)
from ops.charm import (
    CharmBase,
    RelationBrokenEvent,
    RelationChangedEvent,
    RelationDepartedEvent,
)
from ops.framework import Object
from ops.model import ActiveStatus, BlockedStatus, Relation, Unit
from pgconnstr import ConnectionString

from constants import DATABASE_PORT
from utils import new_password

logger = logging.getLogger(__name__)

EXTENSIONS_BLOCKING_MESSAGE = "extensions requested through relation"

ROLES_BLOCKING_MESSAGE = (
    "roles requested through relation, use postgresql_client interface instead"
)


class DbProvides(Object):
    """Defines functionality for the 'provides' side of the 'db' relation.

    Hook events observed:
        - relation-changed
        - relation-departed
        - relation-broken
    """

    def __init__(self, charm: CharmBase, admin: bool = False):
        """Constructor for DbProvides object.

        Args:
            charm: the charm for which this relation is provided
            admin: a boolean defining whether or not this relation has admin permissions, switching
                between "db" and "db-admin" relations.
        """
        if admin:
            self.relation_name = "db-admin"
        else:
            self.relation_name = "db"

        super().__init__(charm, self.relation_name)

        self.framework.observe(
            charm.on[self.relation_name].relation_changed, self._on_relation_changed
        )
        self.framework.observe(
            charm.on[self.relation_name].relation_departed, self._on_relation_departed
        )
        self.framework.observe(
            charm.on[self.relation_name].relation_broken, self._on_relation_broken
        )

        self.admin = admin
        self.charm = charm

    def _on_relation_changed(self, event: RelationChangedEvent) -> None:
        """Handle the legacy db/db-admin relation changed event.

        Generate password and handle user and database creation for the related application.
        """
        # Check for some conditions before trying to access the PostgreSQL instance.
        if (
            "cluster_initialised" not in self.charm._peers.data[self.charm.app]
            or not self.charm._patroni.member_started
        ):
            logger.debug(
                "Deferring on_relation_changed: Cluster not initialized or patroni not running"
            )
            event.defer()
            return

        if not self.charm.unit.is_leader():
            return

        logger.warning(f"DEPRECATION WARNING - `{self.relation_name}` is a legacy interface")

        self.set_up_relation(event.relation)

    def _get_extensions(self, relation: Relation) -> Tuple[List, Set]:
        """Returns the list of required and disabled extensions."""
        requested_extensions = relation.data.get(relation.app, {}).get("extensions", "").split(",")
        for unit in relation.units:
            requested_extensions.extend(
                relation.data.get(unit, {}).get("extensions", "").split(",")
            )
        required_extensions = []
        for extension in requested_extensions:
            if extension != "" and extension not in required_extensions:
                required_extensions.append(extension)
        disabled_extensions = set()
        if required_extensions:
            for extension in required_extensions:
                extension_name = extension.split(":")[0]
                if not self.charm.model.config.get(f"plugin_{extension_name}_enable"):
                    disabled_extensions.add(extension_name)
        return required_extensions, disabled_extensions

    def _get_roles(self, relation: Relation) -> bool:
        """Checks if relation required roles."""
        return "roles" in relation.data.get(relation.app, {})

    def set_up_relation(self, relation: Relation) -> bool:
        """Set up the relation to be used by the application charm."""
        # Do not allow apps requesting extensions to be installed
        # (let them now about config options).
        required_extensions, disabled_extensions = self._get_extensions(relation)
        if disabled_extensions:
            logger.error(
                f"ERROR - `extensions` ({', '.join(disabled_extensions)}) cannot be requested through relations"
                " - Please enable extensions through `juju config` and add the relation again."
            )
            self.charm.unit.status = BlockedStatus(EXTENSIONS_BLOCKING_MESSAGE)
            return False

        if self._get_roles(relation):
            self.charm.unit.status = BlockedStatus(ROLES_BLOCKING_MESSAGE)
            return False

        database = relation.data.get(relation.app, {}).get("database")
        if not database:
            for unit in relation.units:
                unit_database = relation.data.get(unit, {}).get("database")
                if unit_database:
                    database = unit_database
                    break

        if not database:
            logger.warning("Early exit on_relation_changed: No database name provided")
            return False

        try:
            unit_relation_databag = relation.data[self.charm.unit]
            application_relation_databag = relation.data[self.charm.app]

            # Creates the user and the database for this specific relation if it was not already
            # created in a previous relation changed event.
            user = f"relation_id_{relation.id}"
            password = unit_relation_databag.get("password", new_password())
            self.charm.postgresql.create_user(user, password, self.admin)
            plugins = [
                "_".join(plugin.split("_")[1:-1])
                for plugin in self.charm.config.plugin_keys()
                if self.charm.config[plugin]
            ]

            self.charm.postgresql.create_database(
                database, user, plugins=plugins, client_relations=self.charm.client_relations
            )

            # Build the primary's connection string.
            primary = str(
                ConnectionString(
                    host=self.charm.primary_endpoint,
                    dbname=database,
                    port=DATABASE_PORT,
                    user=user,
                    password=password,
                    fallback_application_name=relation.app.name,
                )
            )

            # Build the standbys' connection string.
            standbys = str(
                ConnectionString(
                    host=self.charm.replicas_endpoint,
                    dbname=database,
                    port=DATABASE_PORT,
                    user=user,
                    password=password,
                    fallback_application_name=relation.app.name,
                )
            )

            # Set the data in both application and unit data bag.
            # It's needed to run this logic on every relation changed event
            # setting the data again in the databag, otherwise the application charm that
            # is connecting to this database will receive a "database gone" event from the
            # old PostgreSQL library (ops-lib-pgsql) and the connection between the
            # application and this charm will not work.
            for databag in [application_relation_databag, unit_relation_databag]:
                updates = {
                    "allowed-subnets": self._get_allowed_subnets(relation),
                    "allowed-units": self._get_allowed_units(relation),
                    "host": self.charm.endpoint,
                    "master": primary,
                    "port": DATABASE_PORT,
                    "standbys": standbys,
                    "version": self.charm.postgresql.get_postgresql_version(),
                    "user": user,
                    "password": password,
                    "database": database,
                    "extensions": ",".join(required_extensions),
                }
                databag.update(updates)
        except (
            PostgreSQLCreateDatabaseError,
            PostgreSQLCreateUserError,
            PostgreSQLGetPostgreSQLVersionError,
        ):
            self.charm.unit.status = BlockedStatus(
                f"Failed to initialize {self.relation_name} relation"
            )
            return False

        self._update_unit_status(relation)

        return True

    def _check_for_blocking_relations(self, relation_id: int) -> bool:
        """Checks if there are relations with extensions or roles.

        Args:
            relation_id: current relation to be skipped
        """
        for relname in ["db", "db-admin"]:
            for relation in self.charm.model.relations.get(relname, []):
                if relation.id == relation_id:
                    continue
                for data in relation.data.values():
                    if "extensions" in data or "roles" in data:
                        return True
        return False

    def _on_relation_departed(self, event: RelationDepartedEvent) -> None:
        """Handle the departure of legacy db and db-admin relations.

        Remove unit name from allowed_units key.
        """
        # Check for some conditions before trying to access the PostgreSQL instance.
        if (
            "cluster_initialised" not in self.charm._peers.data[self.charm.app]
            or not self.charm._patroni.member_started
        ):
            logger.debug(
                "Deferring on_relation_departed: Cluster not initialized or patroni not running"
            )
            event.defer()
            return

        # Set a flag to avoid deleting database users when this unit
        # is removed and receives relation broken events from related applications.
        # This is needed because of https://bugs.launchpad.net/juju/+bug/1979811.
        if event.departing_unit == self.charm.unit:
            self.charm._peers.data[self.charm.unit].update({"departing": "True"})
            return

        if not self.charm.unit.is_leader():
            return

        if event.departing_unit.app == self.charm.app:
            # Just run for departing of remote units.
            return

        departing_unit = event.departing_unit.name
        local_unit_data = event.relation.data[self.charm.unit]
        local_app_data = event.relation.data[self.charm.app]

        current_allowed_units = local_unit_data.get("allowed_units", "")

        logger.debug(f"Removing unit {departing_unit} from allowed_units")
        local_app_data["allowed_units"] = local_unit_data["allowed_units"] = " ".join({
            unit for unit in current_allowed_units.split() if unit != departing_unit
        })

    def _on_relation_broken(self, event: RelationBrokenEvent) -> None:
        """Remove the user created for this relation."""
        # Check for some conditions before trying to access the PostgreSQL instance.
        if (
            "cluster_initialised" not in self.charm._peers.data[self.charm.app]
            or not self.charm._patroni.member_started
        ):
            logger.debug(
                "Deferring on_relation_broken: Cluster not initialized or patroni not running"
            )
            event.defer()
            return

        if "departing" in self.charm._peers.data[self.charm.unit]:
            logger.debug("Early exit on_relation_broken: Skipping departing unit")
            return

        if not self.charm.unit.is_leader():
            return

        # Delete the user.
        user = f"relation_id_{event.relation.id}"
        try:
            self.charm.postgresql.delete_user(user)
        except PostgreSQLDeleteUserError:
            self.charm.unit.status = BlockedStatus(
                f"Failed to delete user during {self.relation_name} relation broken event"
            )

        self._update_unit_status(event.relation)

    def _update_unit_status(self, relation: Relation) -> None:
        """# Clean up Blocked status if it's due to extensions request."""
        if self.charm._has_blocked_status and self.charm.unit.status.message in [
            EXTENSIONS_BLOCKING_MESSAGE,
            ROLES_BLOCKING_MESSAGE,
        ]:
            if not self._check_for_blocking_relations(relation.id):
                self.charm.unit.status = ActiveStatus()

    def _get_allowed_subnets(self, relation: Relation) -> str:
        """Build the list of allowed subnets as in the legacy charm."""

        def _csplit(s) -> Iterable[str]:
            if s:
                for b in s.split(","):
                    b = b.strip()
                    if b:
                        yield b

        subnets = set()
        for unit, relation_data in relation.data.items():
            if isinstance(unit, Unit) and not unit.name.startswith(self.model.app.name):
                # Egress-subnets is not always available.
                subnets.update(set(_csplit(relation_data.get("egress-subnets", ""))))
        return ",".join(sorted(subnets))

    def _get_allowed_units(self, relation: Relation) -> str:
        """Build the list of allowed units as in the legacy charm."""
        return ",".join(
            sorted(
                unit.name
                for unit in relation.data
                if isinstance(unit, Unit) and not unit.name.startswith(self.model.app.name)
            )
        )

    def _get_state(self) -> str:
        """Gets the given state for this unit.

        Returns:
            The state of this unit. Can be 'standalone', 'master', or 'standby'.
        """
        if len(self.charm._peers.units) == 0:
            return "standalone"
        if self.charm._patroni.get_primary(unit_name_pattern=True) == self.charm.unit.name:
            return "master"
        else:
            return "standby"
