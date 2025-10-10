# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Postgres client relation hooks & helpers."""

import json
import logging
from typing import TYPE_CHECKING

from charms.data_platform_libs.v1.data_interfaces import (
    DataContractV1,
    RequirerCommonModel,
    ResourceProviderEventHandler,
    ResourceProviderModel,
    ResourceRequestedEvent,
    SecretBool,
    SecretStr,
)
from ops.charm import RelationBrokenEvent, RelationDepartedEvent
from ops.framework import Object
from ops.model import ActiveStatus, BlockedStatus, ModelError, Relation
from pydantic.types import _SecretBase
from single_kernel_postgresql.utils.postgresql import (
    ACCESS_GROUP_RELATION,
    ACCESS_GROUPS,
    INVALID_DATABASE_NAME_BLOCKING_MESSAGE,
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
        self.database = ResourceProviderEventHandler(
            self.charm, self.relation_name, RequirerCommonModel
        )
        self.framework.observe(self.database.on.resource_requested, self._on_resource_requested)

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

    def _get_custom_credentials(
        self, event: ResourceRequestedEvent
    ) -> tuple[str | None, str | None] | None:
        """Check for secret with custom credentials and get values."""
        user = None
        password = None
        try:
            request = event.request
            if request.entity_type == "USER":
                entity_secret = self.charm.model.get_secret(id=request.entity_secret)
                user = entity_secret.get_content().get("username")
                password = entity_secret.get_content().get("password")
                if user in SYSTEM_USERS or user in self.charm.postgresql.list_users():
                    self.charm.unit.status = BlockedStatus(FORBIDDEN_USER_MSG)
                    return
        except ModelError:
            self.charm.unit.status = BlockedStatus(NO_ACCESS_TO_SECRET_MSG)
            return
        return user, password

    def _on_resource_requested(self, event: ResourceRequestedEvent) -> None:
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

        if not (credentials := self._get_custom_credentials(event)):
            return
        user, password = credentials

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

        logger.error(type(event))
        request = event.request

        # Retrieve the database name and extra user roles using the charm library.
        database = request.resource or ""

        # Make sure the relation access-group is added to the list
        extra_user_roles = self._sanitize_extra_roles(request.extra_user_roles)
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

            _, ca, _ = self.charm.tls.get_client_tls_files()
            if not ca:
                ca = ""
            response = ResourceProviderModel(
                salt=event.request.salt,
                request_id=event.request.request_id,
                resource=database,
                username=SecretStr(user),
                password=SecretStr(password),
                endpoints=f"{self.charm.primary_endpoint}:{DATABASE_PORT}",
                uris=SecretStr(
                    f"postgresql://{user}:{password}@{self.charm.primary_endpoint}:{DATABASE_PORT}/{database}"
                ),
                tls=SecretBool(self.charm.is_tls_enabled),
                tls_ca=SecretStr(ca if self.charm.is_tls_enabled else ""),
                version=self.charm.postgresql.get_postgresql_version(),
            )

            # Update the read-only endpoint.
            self.update_read_only_endpoint(event, response, user, password)

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
                if (
                    issubclass(type(e), PostgreSQLCreateDatabaseError)
                    or issubclass(type(e), PostgreSQLCreateUserError)
                )
                and e.message is not None
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
        event: ResourceRequestedEvent | None = None,
        response: ResourceProviderModel | None = None,
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
            if len(relations) > 1:
                response = ResourceProviderModel()
            if response is not None:
                response.read_only_endpoints = endpoints
                # Make sure that the URI will be a secret
                if (
                    secret_fields := self.get_other_app_relation_field(
                        relation.id, "requested-secrets"
                    )
                ) and "read-only-uris" in secret_fields:
                    if not user or not password or not database:
                        user = self.get_this_app_relation_field(relation.id, "username")
                        database = self.get_other_app_relation_field(relation.id, "database")
                        password = self.get_this_app_relation_field(relation.id, "password")

                    if user and password:
                        response.read_only_uris = SecretStr(
                            f"postgresql://{user}:{password}@{endpoints}/{database}"
                        )
                # Reset the creds for the next iteration
                user = None
                password = None
                database = None

                self.database.set_response(relation.id, response)

    def update_tls_flag(self, tls: str) -> None:
        """Update TLS flag and CA in relation databag."""
        if not self.charm.unit.is_leader():
            return

        relations = self.model.relations[self.relation_name]
        ca = None
        if tls == "True":
            _, ca, _ = self.charm.tls.get_client_tls_files()
        if not ca:
            ca = ""

        for relation in relations:
            if self.get_other_app_relation_field(relation.id, "database"):
                response = ResourceProviderModel(
                    tls=SecretBool(tls == "True"),
                    tls_ca=SecretStr(ca),
                )
                self.database.set_response(relation.id, response)

    def _update_unit_status(self, relation: Relation) -> None:
        """Clean up Blocked status if it's due to extensions request."""
        if (
            (
                self.charm._has_blocked_status
                and (
                    self.charm.unit.status.message == INVALID_EXTRA_USER_ROLE_BLOCKING_MESSAGE
                    or self.charm.unit.status.message == INVALID_DATABASE_NAME_BLOCKING_MESSAGE
                )
            )
            and not self.check_for_invalid_extra_user_roles(relation.id)
            and not self.check_for_invalid_database_name(relation.id)
        ):
            self.charm.unit.status = ActiveStatus()
        if (
            self.charm._has_blocked_status
            and "Failed to initialize relation" in self.charm.unit.status.message
        ):
            self.charm.unit.status = ActiveStatus()
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
                    if not self.get_this_app_relation_field(relation.id, "secret-user") and (
                        secret_uri := self.get_other_app_relation_field(
                            relation.id, "requested-entity-secret"
                        )
                    ):
                        content = self.framework.model.get_secret(id=secret_uri).get_content()
                        for key in content:
                            if not self.get_this_app_relation_field(relation.id, "username") and (
                                key in SYSTEM_USERS or key in existing_users
                            ):
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

    def check_for_invalid_database_name(self, relation_id: int) -> bool:
        """Checks if there are relations with invalid database names.

        Args:
            relation_id: current relation to be skipped.
        """
        for relation in self.charm.model.relations.get(self.relation_name, []):
            if relation.id == relation_id:
                continue
            for data in relation.data.values():
                database = data.get("database")
                if database is not None and (
                    len(database) > 49 or database in ["postgres", "template0", "template1"]
                ):
                    return True
        return False

    def get_other_app_relation_field(self, relation_id: int, field: str) -> str | None:
        """Get a field from the other application in the specified relation."""
        relation = self.charm.model.get_relation(self.relation_name, relation_id)
        if relation is None:
            return None
        model = self.database.interface.build_model(
            relation_id, DataContractV1, component=relation.app
        )
        value = None
        for request in model.requests:
            value = getattr(request, field)
            break
        if value is None:
            return value
        value = value.get_secret_value() if issubclass(value.__class__, _SecretBase) else value
        return value

    def get_this_app_relation_field(self, relation_id: int, field: str) -> str | None:
        """Get a field from this application in the specifier relation."""
        model = self.database.interface.build_model(relation_id, DataContractV1)
        value = None
        for request in model.requests:
            value = getattr(request, field)
            break
        if value is None:
            return value
        value = value.get_secret_value() if issubclass(value.__class__, _SecretBase) else value
        return value
