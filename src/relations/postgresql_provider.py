# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Postgres client relation hooks & helpers."""

import json
import logging
from typing import TYPE_CHECKING

from charms.data_platform_libs.v0.data_interfaces import (
    DatabaseProvides,
    DatabaseRequestedEvent,
)
from ops.charm import RelationBrokenEvent, RelationDepartedEvent
from ops.framework import Object
from ops.model import ActiveStatus, BlockedStatus, ModelError, Relation
from single_kernel_postgresql.utils.postgresql import (
    ACCESS_GROUP_RELATION,
    ACCESS_GROUPS,
    INVALID_EXTRA_USER_ROLE_BLOCKING_MESSAGE,
    PostgreSQLCreateDatabaseError,
    PostgreSQLCreateUserError,
    PostgreSQLDeleteUserError,
    PostgreSQLGetPostgreSQLVersionError,
)

from constants import APP_SCOPE, DATABASE_PORT, SYSTEM_USERS, USERNAME_MAPPING_LABEL
from utils import new_password

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from charm import PostgresqlOperatorCharm


# Label not a secret
NO_ACCESS_TO_SECRET_MSG = "Missing grant to requested entity secret"  # noqa: S105
FORBIDDEN_USER_MSG = "Requesting an existing username"


class PostgreSQLProvider(Object):
    """Defines functionality for the 'provides' side of the 'postgresql-client' relation.

    Hook events observed:
        - database-requested
        - relation-broken
    """

    def __init__(self, charm: "PostgresqlOperatorCharm", relation_name: str = "database") -> None:
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

    @staticmethod
    def _sanitize_extra_roles(extra_roles: str | None) -> list[str]:
        """Standardize and sanitize user extra-roles."""
        if extra_roles is None:
            return []

        # Make sure the access-groups are not in the list
        extra_roles_list = [role.lower() for role in extra_roles.split(",")]
        extra_roles_list = [role for role in extra_roles_list if role not in ACCESS_GROUPS]
        return extra_roles_list

    def get_username_mapping(self) -> dict[str, str]:
        """Get a mapping of custom usernames by a relation ID."""
        if username_mapping := self.charm.get_secret(APP_SCOPE, USERNAME_MAPPING_LABEL):
            return json.loads(username_mapping)
        return {}

    def update_username_mapping(self, relation_id: int, username: str | None) -> None:
        """Update a mapping of custom usernames in the application peer secret."""
        if username == f"relation_id_{relation_id}":
            return

        username_mapping = self.get_username_mapping()
        if username and username_mapping.get(str(relation_id)) != username:
            username_mapping[str(relation_id)] = username
        elif not username and username_mapping.get(str(relation_id)):
            del username_mapping[str(relation_id)]
        else:
            # Cache is up to date
            return
        self.charm.set_secret(APP_SCOPE, USERNAME_MAPPING_LABEL, json.dumps(username_mapping))

    def _on_database_requested(self, event: DatabaseRequestedEvent) -> None:
        """Handle the legacy postgresql-client relation changed event.

        Generate password and handle user and database creation for the related application.
        """
        # Check for some conditions before trying to access the PostgreSQL instance.
        if not self.charm.is_cluster_initialised or not self.charm._patroni.primary_endpoint_ready:
            logger.debug(
                "Deferring on_database_requested: Cluster must be initialized before database can be requested"
            )
            event.defer()
            return

        user = None
        password = None
        try:
            if requested_entities := event.requested_entity_secret_content:
                for key, val in requested_entities.items():
                    user = key
                    password = val
                    break
                if user in SYSTEM_USERS or user in self.charm.postgresql.list_users():
                    self.charm.unit.status = BlockedStatus(FORBIDDEN_USER_MSG)
                    return
        except ModelError:
            self.charm.unit.status = BlockedStatus(NO_ACCESS_TO_SECRET_MSG)
            return

        self.update_username_mapping(event.relation.id, user)
        self.charm.update_config()
        for key in self.charm.all_peer_data:
            # We skip the leader so we don't have to wait on the defer
            if (
                key != self.charm.app
                and key != self.charm.unit
                and self.charm.all_peer_data[key].get("user_hash", "")
                != self.charm.generate_user_hash
            ):
                logger.debug("Not all units have synced configuration")
                event.defer()
                return

        # Retrieve the database name and extra user roles using the charm library.
        database = event.database or ""

        # Make sure the relation access-group is added to the list
        extra_user_roles = self._sanitize_extra_roles(event.extra_user_roles)
        extra_user_roles.append(ACCESS_GROUP_RELATION)

        try:
            # Creates the user and the database for this specific relation.
            user = user or f"relation_id_{event.relation.id}"
            password = password or new_password()
            plugins = self.charm.get_plugins()

            self.charm.postgresql.create_database(database, plugins=plugins)

            self.charm.postgresql.create_user(
                user, password, extra_user_roles=extra_user_roles, database=database
            )

            # Share the credentials with the application.
            self.database_provides.set_credentials(event.relation.id, user, password)

            # Set the read/write endpoint.
            self.database_provides.set_endpoints(
                event.relation.id,
                f"{self.charm.primary_endpoint}:{DATABASE_PORT}",
            )

            # Set connection string URI.
            self.database_provides.set_uris(
                event.relation.id,
                f"postgresql://{user}:{password}@{self.charm.primary_endpoint}:{DATABASE_PORT}/{database}",
            )

            # Set TLS flag
            self.database_provides.set_tls(
                event.relation.id,
                "True" if self.charm.is_tls_enabled else "False",
            )

            # Set TLS CA
            if self.charm.is_tls_enabled:
                _, ca, _ = self.charm.tls.get_tls_files()
                self.database_provides.set_tls_ca(event.relation.id, ca)

            # Update the read-only endpoint.
            self.update_read_only_endpoint(event, user, password)

            # Set the database version.
            self.database_provides.set_version(
                event.relation.id, self.charm.postgresql.get_postgresql_version()
            )

            # Set the database name
            self.database_provides.set_database(event.relation.id, database)

            self._update_unit_status(event.relation)

            self.charm.update_config()
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
            return

    def _on_relation_departed(self, event: RelationDepartedEvent) -> None:
        """Set a flag to avoid deleting database users when not wanted."""
        # Set a flag to avoid deleting database users when this unit
        # is removed and receives relation broken events from related applications.
        # This is needed because of https://bugs.launchpad.net/juju/+bug/1979811.
        if event.departing_unit == self.charm.unit and self.charm._peers:
            self.charm._peers.data[self.charm.unit].update({"departing": "True"})

    def _on_relation_broken(self, event: RelationBrokenEvent) -> None:
        """Remove the user created for this relation."""
        # Check for some conditions before trying to access the PostgreSQL instance.
        if (
            not self.charm._peers
            or not self.charm.is_cluster_initialised
            or not self.charm._patroni.member_started
        ):
            logger.debug(
                "Deferring on_relation_broken: Cluster must be initialized before user can be deleted"
            )
            event.defer()
            return

        self._update_unit_status(event.relation)

        if self.charm.is_unit_departing:
            logger.debug("Early exit on_relation_broken: Skipping departing unit")
            return

        user = self.get_username_mapping().get(
            str(event.relation.id), f"relation_id_{event.relation.id}"
        )
        if not self.charm.unit.is_leader():
            if user in self.charm.postgresql.list_users():
                logger.debug("Deferring on_relation_broken: user was not deleted yet")
                event.defer()
            else:
                self.charm.update_config()
            return

        # Delete the user.
        try:
            self.charm.postgresql.delete_user(user)
        except PostgreSQLDeleteUserError as e:
            logger.exception(e)
            self.charm.unit.status = BlockedStatus(
                f"Failed to delete user during {self.relation_name} relation broken event"
            )

        self.update_username_mapping(event.relation.id, None)
        self.charm.update_config()

    def update_read_only_endpoint(
        self,
        event: DatabaseRequestedEvent | None = None,
        user: str | None = None,
        password: str | None = None,
        database: str | None = None,
    ) -> None:
        """Set the read-only endpoint only if there are replicas."""
        if not self.charm.unit.is_leader():
            return

        # If there are no replicas, remove the read-only endpoint.
        endpoints = (
            f"{self.charm.replicas_endpoint}:{DATABASE_PORT}"
            if self.charm._peers and len(self.charm._peers.units) > 0
            else f"{self.charm.primary_endpoint}:{DATABASE_PORT}"
        )

        # Get the current relation or all the relations
        # if this is triggered by another type of event.
        relations = [event.relation] if event else self.model.relations[self.relation_name]
        if not event:
            user = None
            password = None
            database = None

        for relation in relations:
            self.database_provides.set_read_only_endpoints(
                relation.id,
                endpoints,
            )
            # Make sure that the URI will be a secret
            if (
                secret_fields := self.database_provides.fetch_relation_field(
                    relation.id, "requested-secrets"
                )
            ) and "read-only-uris" in secret_fields:
                if not user or not password or not database:
                    user = self.database_provides.fetch_my_relation_field(relation.id, "username")
                    database = self.database_provides.fetch_relation_field(relation.id, "database")
                    password = self.database_provides.fetch_my_relation_field(
                        relation.id, "password"
                    )

                if user and password:
                    self.database_provides.set_read_only_uris(
                        relation.id,
                        f"postgresql://{user}:{password}@{endpoints}/{database}",
                    )
            # Reset the creds for the next iteration
            user = None
            password = None
            database = None

    def update_tls_flag(self, tls: str) -> None:
        """Update TLS flag and CA in relation databag."""
        if not self.charm.unit.is_leader():
            return

        relations = self.model.relations[self.relation_name]
        if tls == "True":
            _, ca, _ = self.charm.tls.get_tls_files()
        else:
            ca = ""

        for relation in relations:
            if self.database_provides.fetch_relation_field(relation.id, "database"):
                self.database_provides.set_tls(relation.id, tls)
                self.database_provides.set_tls_ca(relation.id, ca)

    def _update_unit_status(self, relation: Relation) -> None:
        """Clean up Blocked status if it's due to extensions request."""
        if self.charm._has_blocked_status and self.charm.unit.status.message in [
            INVALID_EXTRA_USER_ROLE_BLOCKING_MESSAGE,
            NO_ACCESS_TO_SECRET_MSG,
            FORBIDDEN_USER_MSG,
        ]:
            if self.check_for_invalid_extra_user_roles(relation.id):
                self.charm.unit.status = BlockedStatus(INVALID_EXTRA_USER_ROLE_BLOCKING_MESSAGE)
                return
            existing_users = self.charm.postgresql.list_users()
            for relation in self.charm.model.relations.get(self.relation_name, []):
                try:
                    # Relation is not established and custom user was requested
                    if not self.database_provides.fetch_my_relation_field(
                        relation.id, "secret-user"
                    ) and (
                        secret_uri := self.database_provides.fetch_relation_field(
                            relation.id, "requested-entity-secret"
                        )
                    ):
                        content = self.framework.model.get_secret(id=secret_uri).get_content()
                        for key in content:
                            if not self.database_provides.fetch_my_relation_field(
                                relation.id, "username"
                            ) and (key in SYSTEM_USERS or key in existing_users):
                                logger.warning(
                                    f"Relation {relation.id} is still requesting a forbidden user"
                                )
                                self.charm.unit.status = BlockedStatus(FORBIDDEN_USER_MSG)
                                return
                except ModelError:
                    logger.warning(f"Relation {relation.id} still cannot access the set secret")
                    self.charm.unit.status = BlockedStatus(NO_ACCESS_TO_SECRET_MSG)
                    return
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
                extra_user_roles = self._sanitize_extra_roles(extra_user_roles)
                for extra_user_role in extra_user_roles:
                    if (
                        extra_user_role not in valid_privileges
                        and extra_user_role not in valid_roles
                    ):
                        return True
        return False
