# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Library containing the implementation of the legacy db and db-admin relations."""


import logging
from typing import Iterable

from charms.postgresql.v0.postgresql_helpers import (
    connect_to_database,
    create_database,
    create_user,
    drop_user,
    get_postgresql_version,
)
from ops.charm import (
    CharmBase,
    RelationBrokenEvent,
    RelationChangedEvent,
    RelationDepartedEvent,
)
from ops.framework import Object
from ops.model import BlockedStatus, Relation, Unit
from pgconnstr import ConnectionString

logger = logging.getLogger(__name__)


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

        self.charm = charm
        self.admin = admin

    def _on_relation_changed(self, event: RelationChangedEvent) -> None:
        """Handle the legacy db/db-admin relation changed event.

        Generate password and handle user and database creation for the related application.
        """
        # Check for some conditions before trying to access the PostgreSQL instance.
        if (
            "cluster_initialised" not in self.charm._peers.data[self.charm.app]
            or not self.charm._patroni.member_started
        ):
            event.defer()
            return

        if not self.charm.unit.is_leader():
            return

        logger.warning(f"DEPRECATION WARNING - `{event.relation.name}` is a legacy interface")

        unit_relation_databag = event.relation.data[self.charm.unit]
        application_relation_databag = event.relation.data[self.charm.app]

        if "extensions" in unit_relation_databag or "extensions" in application_relation_databag:
            logger.error(
                "ERROR - `extensions` cannot be requested through relations"
                " - they should be installed through a database charm config in the future"
            )
            self.charm.unit.status = BlockedStatus("extensions requested through relation")
            return

        # Retrieve
        database = event.relation.data[event.app].get(
            "database", event.relation.data[event.unit].get("database")
        )
        # Sometimes a relation changed event is triggered,
        # and it doesn't have a database name in it.
        if not database:
            logger.warning("No database name provided")
            event.defer()
            return

        # Connect to the PostgreSQL instance to later create a user and the database.
        connection = connect_to_database(
            "postgres",
            "postgres",
            self.charm.primary_endpoint,
            self.charm._get_postgres_password(),
        )

        # Define the new user credentials.
        user = f"relation_id_{event.relation.id}"
        password = unit_relation_databag.get("password", self.charm._new_password())

        # Creates the user and the database for this specific relation if it was not already
        # created in a previous relation changed event.
        # Use the relation name to request or not a superuser (admin flag).
        create_user(connection, user, password, admin=self.admin)
        create_database(connection, database, user)

        # Build the primary's connection string.
        primary = str(
            ConnectionString(
                host=self.charm.primary_endpoint,
                dbname=database,
                port=5432,
                user=user,
                password=password,
                fallback_application_name=event.app.name,
            )
        )
        # Build the standbys' connection string.
        standbys = str(
            ConnectionString(
                host=self.charm.replicas_endpoint,
                dbname=database,
                port=5432,
                user=user,
                password=password,
                fallback_application_name=event.app.name,
            )
        )

        # Set the data in both application and unit data bag.
        # It 's needed to run this logic on every relation changed event
        # setting the data again in the databag, otherwise the application charm that
        # is connecting to this database will receive a "database gone" event from the
        # old PostgreSQL library (ops-lib-pgsql) and the connection between the
        # application and this charm will not work.
        for databag in [application_relation_databag, unit_relation_databag]:
            # This list of subnets is not being filled correctly yet.
            databag["allowed-subnets"] = self._get_allowed_subnets(event.relation)
            databag["allowed-units"] = self._get_allowed_units(event.relation)
            databag["host"] = self.charm.primary_endpoint
            databag["master"] = primary
            databag["port"] = "5432"
            databag["standbys"] = standbys
            databag["state"] = self._get_state()
            databag["version"] = get_postgresql_version(connection)
            databag["user"] = user
            databag["password"] = password
            databag["database"] = database

        connection.close()

    def _get_allowed_units(self, relation: Relation) -> str:
        return ",".join(
            sorted(
                unit.name
                for unit in relation.data
                if isinstance(unit, Unit) and not unit.name.startswith(self.model.app.name)
            )
        )

    def _get_allowed_subnets(self, relation: Relation) -> str:
        def _csplit(s) -> Iterable[str]:
            if s:
                for b in s.split(","):
                    b = b.strip()
                    if b:
                        yield b

        subnets = set()
        for unit, reldata in relation.data.items():
            logger.warning(f"Checking subnets for {unit}")
            logger.warning(reldata)
            if isinstance(unit, Unit) and not unit.name.startswith(self.model.app.name):
                # NB. egress-subnets is not always available.
                subnets.update(set(_csplit(reldata.get("egress-subnets", ""))))
        return ",".join(sorted(subnets))

    def _get_state(self) -> str:
        """Gets the given state for this unit.

        Returns:
            The described state of this unit. Can be 'standalone', 'master', or 'standby'.
        """
        if len(self.charm._peers.units) == 0:
            return "standalone"
        if self.charm._patroni.get_primary(unit_name_pattern=True) == self.charm.unit.name:
            return "master"
        else:
            return "standby"

    def _on_relation_departed(self, event: RelationDepartedEvent) -> None:
        """Handle the departure of legacy db and db-admin relations.

        Remove unit name from allowed_units key.
        """
        if not self.charm.unit.is_leader():
            return

        if event.departing_unit.app == self.charm.app:
            # Just run for departing of remote units
            return

        departing_unit = event.departing_unit.name
        local_unit_data = event.relation.data[self.charm.unit]
        local_app_data = event.relation.data[self.charm.app]

        current_allowed_units = local_unit_data.get("allowed_units", "")

        logger.debug(f"Removing unit {departing_unit} from allowed_units")
        local_app_data["allowed_units"] = local_unit_data["allowed_units"] = " ".join(
            {unit for unit in current_allowed_units.split() if unit != departing_unit}
        )

    def _on_relation_broken(self, event: RelationBrokenEvent) -> None:
        # Remove the user created for this relation.
        # Check for some conditions before trying to access the PostgreSQL instance.
        if (
            "cluster_initialised" not in self.charm._peers.data[self.charm.app]
            or not self.charm._patroni.member_started
        ):
            event.defer()
            return

        if not self.charm.unit.is_leader():
            return

        application_relation_databag = event.relation.data[self.charm.app]
        database = application_relation_databag.get("database")

        # Connect to the PostgreSQL instance to later create a user and the database.
        connection = connect_to_database(
            database, "postgres", self.charm.primary_endpoint, self.charm._get_postgres_password()
        )

        # Drop the user.
        user = f"relation_id_{event.relation.id}"
        drop_user(connection, user)
