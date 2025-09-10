# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Logical Replication implementation.

TODO: add description after specification is accepted.
"""

import json
import logging
from typing import (
    TYPE_CHECKING,
)

from ops import (
    BlockedStatus,
    EventBase,
    Object,
    Relation,
    RelationBrokenEvent,
    RelationChangedEvent,
    RelationDepartedEvent,
    RelationJoinedEvent,
    Secret,
    SecretChangedEvent,
    SecretNotFoundError,
)
from tenacity import Retrying, stop_after_delay, wait_fixed

from utils import new_password

if TYPE_CHECKING:
    from charm import PostgresqlOperatorCharm

logger = logging.getLogger(__name__)

LOGICAL_REPLICATION_OFFER_RELATION = "logical-replication-offer"
LOGICAL_REPLICATION_RELATION = "logical-replication"
SECRET_LABEL = "logical-replication-relation"  # noqa: S105
LOGICAL_REPLICATION_VALIDATION_ERROR_STATUS = "Logical replication setup is invalid. Check logs"


class PostgreSQLLogicalReplication(Object):
    """Defines the logical-replication logic."""

    def _identity(self) -> str:
        """Return unique identity of this application in the model."""
        return f"{self.model.uuid}:{self.model.app.name}"

    def __init__(self, charm: "PostgresqlOperatorCharm"):
        super().__init__(charm, "postgresql_logical_replication")
        self.charm = charm
        # Relations
        self.charm.framework.observe(
            self.charm.on[LOGICAL_REPLICATION_OFFER_RELATION].relation_joined,
            self._on_offer_relation_joined,
        )
        self.charm.framework.observe(
            self.charm.on[LOGICAL_REPLICATION_OFFER_RELATION].relation_changed,
            self._on_offer_relation_changed,
        )
        self.charm.framework.observe(
            self.charm.on[LOGICAL_REPLICATION_OFFER_RELATION].relation_departed,
            self._on_offer_relation_departed,
        )
        self.charm.framework.observe(
            self.charm.on[LOGICAL_REPLICATION_OFFER_RELATION].relation_broken,
            self._on_offer_relation_broken,
        )
        self.charm.framework.observe(
            self.charm.on[LOGICAL_REPLICATION_RELATION].relation_joined, self._on_relation_joined
        )
        self.charm.framework.observe(
            self.charm.on[LOGICAL_REPLICATION_RELATION].relation_changed, self._on_relation_changed
        )
        self.charm.framework.observe(
            self.charm.on[LOGICAL_REPLICATION_RELATION].relation_departed,
            self._on_relation_departed,
        )
        self.charm.framework.observe(
            self.charm.on[LOGICAL_REPLICATION_RELATION].relation_broken, self._on_relation_broken
        )
        # Events
        self.framework.observe(self.charm.on.secret_changed, self._on_secret_changed)

    # region Relations

    def _on_offer_relation_joined(self, event: RelationJoinedEvent) -> None:
        if not self.charm.unit.is_leader():
            logger.debug(
                f"{LOGICAL_REPLICATION_OFFER_RELATION} #{event.relation.id} join early exit due to unit not being a leader"
            )
            return
        if not self.charm.primary_endpoint:
            logger.debug(
                f"Deferring {LOGICAL_REPLICATION_OFFER_RELATION} #{event.relation.id} join due to primary unavailability"
            )
            event.defer()
            return

        secret = self._get_secret(event.relation.id)
        logger.debug(
            f"Sharing logical replciation secret to the {LOGICAL_REPLICATION_OFFER_RELATION} #{event.relation.id}"
        )
        secret.grant(event.relation)

        self._save_published_resources_info(str(event.relation.id), secret.id, {})  # type: ignore
        event.relation.data[self.model.app]["secret-id"] = secret.id  # type: ignore

    def _on_offer_relation_changed(self, event: RelationChangedEvent) -> None:
        if not self.charm.unit.is_leader():
            logger.debug(
                f"{LOGICAL_REPLICATION_OFFER_RELATION} #{event.relation.id} change early exit due to unit not being a leader"
            )
            return
        if not self.charm.primary_endpoint:
            logger.debug(
                f"Deferring {LOGICAL_REPLICATION_OFFER_RELATION} #{event.relation.id} change due to primary unavailability"
            )
            event.defer()
            return
        self._process_offer(event.relation)

    def _on_offer_relation_departed(self, event: RelationDepartedEvent) -> None:
        if event.departing_unit == self.charm.unit and self.charm._peers is not None:
            logger.debug(
                f"Marking unit as departed for {LOGICAL_REPLICATION_OFFER_RELATION} #{event.relation.id} to skip break"
            )
            self.charm.unit_peer_data.update({"departing": "True"})

    def _on_offer_relation_broken(self, event: RelationBrokenEvent) -> None:
        if not self.charm._peers or self.charm.is_unit_departing:
            logger.debug(
                f"{LOGICAL_REPLICATION_OFFER_RELATION} #{event.relation.id} break early exit due to unit departure"
            )
            return
        if not self.charm.unit.is_leader():
            logger.debug(
                f"{LOGICAL_REPLICATION_OFFER_RELATION} #{event.relation.id} break early exit due to unit not being a leader"
            )
            return
        if not self.charm.primary_endpoint:
            logger.debug(
                f"Deferring {LOGICAL_REPLICATION_OFFER_RELATION} #{event.relation.id} break due to primary unavailability"
            )
            event.defer()
            return

        published_resources = json.loads(
            self.charm.app_peer_data.get("logical-replication-published-resources", "{}")
        )
        active_relation_ids = [
            str(relation.id)
            for relation in self.model.relations.get(LOGICAL_REPLICATION_OFFER_RELATION, ())
        ]

        for relation_id, relation_resources in published_resources.copy().items():
            if relation_id in active_relation_ids:
                continue
            logger.info(
                f"Cleaning up published logical replication resources for the redundant {LOGICAL_REPLICATION_OFFER_RELATION} #{relation_id}"
            )
            try:
                secret = self.model.get_secret(id=relation_resources["secret-id"])
                self.charm.postgresql.delete_user(secret.peek_content()["username"])
                secret.remove_all_revisions()
            except SecretNotFoundError:
                pass
            for database, publication in relation_resources["publications"].items():
                self.charm.postgresql.drop_publication(database, publication["publication-name"])
            del published_resources[relation_id]
            self.charm.app_peer_data["logical-replication-published-resources"] = json.dumps(
                published_resources
            )

        self.charm.update_config()

    def _on_relation_joined(self, event: RelationJoinedEvent) -> None:
        if not self.charm.unit.is_leader():
            logger.debug(
                f"{LOGICAL_REPLICATION_RELATION} #{event.relation.id} join early exit due to unit not being a leader"
            )
            return
        if self.charm.app_peer_data.get("logical-replication-validation") == "ongoing":
            logger.debug(
                f"Deferring {LOGICAL_REPLICATION_RELATION} #{event.relation.id} join due to still ongoing logical replication config validation"
            )
            event.defer()
            return
        if self.charm.app_peer_data.get("logical-replication-validation") == "error":
            logger.debug(
                f"{LOGICAL_REPLICATION_RELATION} #{event.relation.id} join early exit due to validation error"
            )
            return
        if not self._validate_subscription_request():
            return
        event.relation.data[self.model.app]["subscription-request"] = (
            self.charm.config.logical_replication_subscription_request or ""
        )
        # Share our identity with the publisher to prevent cyclic replication
        event.relation.data[self.model.app]["requester-id"] = self._identity()

    def _on_relation_changed(self, event: RelationChangedEvent) -> None:
        if not self._relation_changed_checks(event):
            return

        for error in json.loads(event.relation.data[event.app].get("errors", "[]")):
            logger.error(
                f"Got logical replication error from the publisher in {LOGICAL_REPLICATION_RELATION} #{event.relation.id}: {error}"
            )
            self.charm.unit.status = BlockedStatus(LOGICAL_REPLICATION_VALIDATION_ERROR_STATUS)

        secret_content = self.model.get_secret(
            id=event.relation.data[event.app]["secret-id"]
        ).get_content(refresh=True)
        subscriptions = self._subscriptions_info()
        publications = json.loads(event.relation.data[event.app].get("publications", "{}"))

        for database, publication in publications.items():
            subscription_name = self._subscription_name(event.relation.id, database)
            if database in subscriptions:
                self.charm.postgresql.refresh_subscription(database, subscription_name)
                logger.info(
                    f"Refreshed subscription {subscription_name} in database {database} due to relation change"
                )
            else:
                publication_name = publication["publication-name"]
                for attempt in Retrying(
                    stop=stop_after_delay(120), wait=wait_fixed(3), reraise=True
                ):
                    with attempt:
                        self.charm.postgresql.create_subscription(
                            subscription_name,
                            secret_content["primary"],
                            database,
                            secret_content["username"],
                            secret_content["password"],
                            publication_name,
                            publication["replication-slot-name"],
                        )
                logger.info(
                    f"Created new subscription {subscription_name} for publication {publication_name} in database {database}"
                )
                subscriptions[database] = subscription_name

        for database, subscription in subscriptions.copy().items():
            if database in publications:
                continue
            self.charm.postgresql.drop_subscription(database, subscription)
            logger.info(f"Dropped redundant subscription {subscription} from database {database}")
            del subscriptions[database]

        self.charm.app_peer_data["logical-replication-subscriptions"] = json.dumps({
            str(event.relation.id): subscriptions
        })
        # Rebuild subscribed upstream provenance after any changes
        self._rebuild_subscribed_upstream()

    def _on_relation_departed(self, event: RelationDepartedEvent) -> None:
        if event.departing_unit == self.charm.unit and self.charm._peers is not None:
            self.charm.unit_peer_data.update({"departing": "True"})

    def _on_relation_broken(self, event: RelationBrokenEvent) -> None:
        if not self.charm._peers or self.charm.is_unit_departing:
            logger.debug(f"{LOGICAL_REPLICATION_RELATION} break skipped due to departing unit")
            return
        if not self.charm.unit.is_leader():
            logger.debug(
                f"{LOGICAL_REPLICATION_RELATION} #{event.relation.id} break early exit due to unit not being a leader"
            )
            return
        if not self.charm.primary_endpoint:
            logger.debug(
                f"Deferring {LOGICAL_REPLICATION_RELATION} break until primary is available"
            )
            event.defer()
            return

        for database, subscription in self._subscriptions_info().items():
            self.charm.postgresql.drop_subscription(database, subscription)
            logger.info(
                f"Dropped subscription {subscription} from database {database} due to relation break"
            )
        self.charm.app_peer_data["logical-replication-subscriptions"] = ""
        # Clear provenance as subscriptions are gone
        self._rebuild_subscribed_upstream()

    # endregion

    def _rebuild_subscribed_upstream(self) -> None:
        """Aggregate upstream provenance for all subscribed tables.

        Stores mapping in app peer data as logical-replication-subscribed-upstream
        with keys formatted as "<database>:<schema>.<table>" and values equal to
        the upstream identity "<model_uuid>:<app_name>".
        """
        mapping: dict[str, str] = {}
        for relation in self.model.relations.get(LOGICAL_REPLICATION_RELATION, ()):
            pubs = json.loads(relation.data[relation.app].get("publications", "{}"))
            for database, pub in pubs.items():
                upstream_by_table = pub.get("upstream", {}) if isinstance(pub, dict) else {}
                publisher_id = pub.get("publisher-id", "") if isinstance(pub, dict) else ""
                for schematable in pub.get("tables", []):
                    upstream = upstream_by_table.get(schematable) or publisher_id
                    if upstream:
                        mapping[f"{database}:{schematable}"] = upstream
        self.charm.app_peer_data["logical-replication-subscribed-upstream"] = json.dumps(mapping)

    def _get_subscribed_upstream(self) -> dict[str, str]:
        try:
            return json.loads(
                self.charm.app_peer_data.get("logical-replication-subscribed-upstream", "{}")
            )
        except json.JSONDecodeError:
            return {}

    # region Events

    def _on_secret_changed(self, event: SecretChangedEvent) -> None:
        if not self.charm.unit.is_leader():
            logger.debug(
                "Logical replication secret change early exit due to unit not being a leader"
            )
            return
        if not self.charm.primary_endpoint:
            logger.debug("Deferring logical replication secret change until primary is available")
            event.defer()
            return

        if (
            relation := self.model.get_relation(LOGICAL_REPLICATION_RELATION)
        ) and event.secret.label.startswith(SECRET_LABEL):  # type: ignore
            logger.info("Logical replication secret changed, updating subscriptions")
            secret_content = self.model.get_secret(
                id=relation.data[relation.app]["secret-id"], label=SECRET_LABEL
            ).get_content(refresh=True)
            for database, subscription in self._subscriptions_info().items():
                self.charm.postgresql.update_subscription(
                    database,
                    subscription,
                    secret_content["primary"],
                    secret_content["username"],
                    secret_content["password"],
                )

    # endregion

    def apply_changed_config(self, event: EventBase) -> bool:
        """Validate & apply (relation) logical_replication_subscription_request config parameter."""
        if not self.charm.unit.is_leader():
            return True
        if not self.charm.primary_endpoint:
            logger.debug(
                "Marking logical replication config validation as ongoing and deferring event until primary as available"
            )
            self.charm.app_peer_data["logical-replication-validation"] = "ongoing"
            event.defer()
            return False
        if self._validate_subscription_request():
            self._apply_updated_subscription_request()
        return True

    def retry_validations(self) -> None:
        """Run recurrent logical replication validation attempt.

        For subscribers - try to validate & apply subscription request.
        For publishers - try to validate & process all the offer relations.
        """
        if not self.charm.unit.is_leader() or not self.charm.primary_endpoint:
            return
        if (
            self.charm.app_peer_data.get("logical-replication-validation") == "error"
            and self._validate_subscription_request()
        ):
            self._apply_updated_subscription_request()
        for relation in self.model.relations.get(LOGICAL_REPLICATION_OFFER_RELATION, ()):
            if json.loads(relation.data[self.model.app].get("errors", "[]")):
                self._process_offer(relation)

    def has_remote_publisher_errors(self) -> bool:
        """Check if remote publisher in logical-replication relation has any errors."""
        return bool(
            relation := self.model.get_relation(LOGICAL_REPLICATION_RELATION)
        ) and json.loads(relation.data[relation.app].get("errors", "[]"))

    def replication_slots(self) -> dict[str, str]:
        """Get list of all managed replication slots.

        Returns: dictionary in <slot>: <database> format.
        """
        return {
            publication["replication-slot-name"]: database
            for resources in json.loads(
                self.charm.app_peer_data.get("logical-replication-published-resources", "{}")
            ).values()
            for database, publication in resources["publications"].items()
        }

    def _apply_updated_subscription_request(self) -> None:
        if not (relation := self.model.get_relation(LOGICAL_REPLICATION_RELATION)):
            return
        logger.debug(
            "Logical replication config validation is passed, applying config to the active relations"
        )
        subscription_request_config = json.loads(
            self.charm.config.logical_replication_subscription_request or "{}"
        )
        subscriptions = self._subscriptions_info()
        relation.data[self.model.app]["subscription-request"] = (  # type: ignore
            self.charm.config.logical_replication_subscription_request
        )
        for database, subscription in subscriptions.copy().items():
            if database in subscription_request_config:
                continue
            self.charm.postgresql.drop_subscription(database, subscription)
            logger.info(f"Dropped redundant subscription {subscription} from database {database}")
            del subscriptions[database]
        self.charm.app_peer_data["logical-replication-subscriptions"] = json.dumps({
            str(relation.id): subscriptions
        })

    def _validate_subscription_request(self) -> bool:
        try:
            subscription_request_config = json.loads(
                self.charm.config.logical_replication_subscription_request or "{}"
            )
        except json.JSONDecodeError as err:
            return self._fail_validation(f"JSON decode error {err}")

        relation = self.model.get_relation(LOGICAL_REPLICATION_RELATION)
        subscription_request_relation = (
            json.loads(relation.data[self.model.app].get("subscription-request", "{}"))
            if relation
            else {}
        )

        for database, schematables in subscription_request_config.items():
            if not self.charm.postgresql.database_exists(database):
                return self._fail_validation(f"database {database} doesn't exist")
            for schematable in schematables:
                try:
                    schema, table = schematable.split(".")
                except ValueError:
                    return self._fail_validation(f"table format isn't right at {schematable}")
                if not self.charm.postgresql.table_exists(database, schema, table):
                    return self._fail_validation(
                        f"table {schematable} in database {database} doesn't exist"
                    )
                already_subscribed = (
                    database in subscription_request_relation
                    and schematable in subscription_request_relation[database]
                )
                if not already_subscribed and not self.charm.postgresql.is_table_empty(
                    database, schema, table
                ):
                    return self._fail_validation(
                        f"table {schematable} in database {database} isn't empty"
                    )

        self.charm.app_peer_data["logical-replication-validation"] = ""
        return True

    def _fail_validation(self, message: str | None = None) -> bool:
        if message:
            logger.error(f"Logical replication validation: {message}")
        self.charm.app_peer_data["logical-replication-validation"] = "error"
        self.charm.unit.status = BlockedStatus(LOGICAL_REPLICATION_VALIDATION_ERROR_STATUS)
        return False

    def _validate_new_publication(
        self,
        database: str,
        schematables: list[str],
        publication_schematables: list[str] | None = None,
    ) -> str | None:
        if not self.charm.postgresql.database_exists(database):
            return f"database {database} doesn't exist"
        for schematable in schematables:
            if publication_schematables is not None and schematable in publication_schematables:
                continue
            schema, table = schematable.split(".")
            if not self.charm.postgresql.table_exists(database, schema, table):
                return f"table {schematable} in database {database} doesn't exist"
        return None

    def _relation_changed_checks(self, event: RelationChangedEvent) -> bool:
        if not self.charm.unit.is_leader():
            logger.debug(
                f"{LOGICAL_REPLICATION_RELATION} #{event.relation.id} change early exit due to unit not being a leader"
            )
            return False
        if not event.relation.data[event.app].get("secret-id"):
            logger.warning(
                f"{LOGICAL_REPLICATION_RELATION} #{event.relation.id} change early exit due to secret absence in remote application bag (unusual behavior)"
            )
            return False
        if not self.charm.primary_endpoint:
            logger.debug(
                f"Deferring {LOGICAL_REPLICATION_RELATION} #{event.relation.id} change due to primary unavailability"
            )
            event.defer()
            return False
        return True

    def _process_offer(self, relation: Relation) -> None:  # noqa: C901
        logger.debug(
            f"Started processing offer for {LOGICAL_REPLICATION_OFFER_RELATION} #{relation.id}"
        )

        subscriptions_request = json.loads(
            relation.data[relation.app].get("subscription-request", "{}")
        )
        requester_id = relation.data[relation.app].get("requester-id", "")
        publications = json.loads(relation.data[self.model.app].get("publications", "{}"))
        secret = self._get_secret(relation.id)
        user = secret.peek_content()["username"]
        errors = []
        subscribed_upstream = self._get_subscribed_upstream()

        for database, publication in publications.copy().items():
            if database in subscriptions_request:
                continue
            logger.info(
                f"Dropping redundant publication {publication['publication-name']} in database {database} from {LOGICAL_REPLICATION_OFFER_RELATION} #{relation.id}"
            )
            self.charm.postgresql.drop_publication(database, publication["publication-name"])
            del publications[database]
            logger.info(
                f"Revoking replication privileges on database {database} from user {user} from {LOGICAL_REPLICATION_OFFER_RELATION} #{relation.id}"
            )
            self.charm.postgresql.revoke_replication_privileges(
                user, database, publication["tables"]
            )

        for database, tables in subscriptions_request.items():
            # Cycle detection: if our local upstream for any requested table equals requester, reject
            cycle_detected = False
            upstream_map: dict[str, str] = {}
            for schematable in tables:
                key = f"{database}:{schematable}"
                local_upstream = subscribed_upstream.get(key) or self._identity()
                upstream_map[schematable] = local_upstream
                if requester_id and requester_id == local_upstream:
                    cycle_detected = True
            if cycle_detected:
                error = (
                    f"cyclic logical replication detected for database {database}: "
                    f"requested tables would replicate back to their upstream ({requester_id})"
                )
                errors.append(error)
                logger.error(
                    f"Cannot create/alter publication for {LOGICAL_REPLICATION_OFFER_RELATION} #{relation.id}: {error}"
                )
                # Skip creating/altering this publication to avoid loop
                continue

            if database not in publications:
                if validation_error := self._validate_new_publication(database, tables):
                    errors.append(validation_error)
                    logger.error(
                        f"Cannot create new publication for {LOGICAL_REPLICATION_OFFER_RELATION} #{relation.id}: {validation_error}"
                    )
                    continue
                publication_name = self._publication_name(relation.id, database)
                if self.charm.postgresql.publication_exists(database, publication_name):
                    error = f"conflicting publication {publication_name} in database {database}"
                    errors.append(error)
                    logger.error(
                        f"Cannot create new publication for {LOGICAL_REPLICATION_OFFER_RELATION} #{relation.id}: {error}"
                    )
                    continue
                logger.info(
                    f"Granting replication privileges on database {database} for user {user} for {LOGICAL_REPLICATION_OFFER_RELATION} #{relation.id}"
                )
                self.charm.postgresql.grant_replication_privileges(user, database, tables)
                logger.info(
                    f"Creating new publication {publication_name} for tables {', '.join(tables)} in database {database} for {LOGICAL_REPLICATION_OFFER_RELATION} #{relation.id}"
                )
                self.charm.postgresql.create_publication(database, publication_name, tables)
                publications[database] = {
                    "publication-name": publication_name,
                    "replication-slot-name": self._replication_slot_name(relation.id, database),
                    "tables": tables,
                    "publisher-id": self._identity(),
                    "upstream": upstream_map,
                }
            elif sorted(publication_tables := publications[database]["tables"]) != sorted(tables):
                publication_name = publications[database]["publication-name"]
                if validation_error := self._validate_new_publication(
                    database, tables, publication_tables
                ):
                    errors.append(validation_error)
                    logger.error(
                        f"Cannot alter publication {publication_name} for {LOGICAL_REPLICATION_OFFER_RELATION} #{relation.id}: {validation_error}"
                    )
                    continue
                if not self.charm.postgresql.publication_exists(database, publication_name):
                    errors.append(
                        f"managed publication {publication_name} in database {database} can't be found"
                    )
                    logger.error(
                        f"Can't find managed publication {publication_name} in database {database} for {LOGICAL_REPLICATION_OFFER_RELATION} #{relation.id}"
                    )
                    continue
                logger.info(
                    f"Altering replication privileges on database {database} for user {user} for {LOGICAL_REPLICATION_OFFER_RELATION} #{relation.id}"
                )
                self.charm.postgresql.grant_replication_privileges(
                    user, database, tables, publication_tables
                )
                logger.info(
                    f"Altering publication {publication_name} tables from {','.join(publication_tables)} to {','.join(tables)} in database {database} for {LOGICAL_REPLICATION_OFFER_RELATION} #{relation.id}"
                )
                self.charm.postgresql.alter_publication(database, publication_name, tables)
                publications[database]["tables"] = tables
                publications[database]["publisher-id"] = self._identity()
                publications[database]["upstream"] = upstream_map
            else:
                # Tables unchanged; still update provenance and publisher id to propagate upstream
                publications[database]["publisher-id"] = self._identity()
                publications[database]["upstream"] = upstream_map
            self._save_published_resources_info(str(relation.id), secret.id, publications)  # type: ignore
            relation.data[self.model.app]["publications"] = json.dumps(publications)

        self._save_published_resources_info(str(relation.id), secret.id, publications)  # type: ignore
        relation.data[self.model.app].update({
            "errors": json.dumps(errors),
            "publications": json.dumps(publications),
        })
        self.charm.update_config()

        logger.debug(
            f"Successfully processed offer for {LOGICAL_REPLICATION_OFFER_RELATION} #{relation.id}"
        )

    def _publication_name(self, relation_id: int, database: str) -> str:
        return f"relation_{relation_id}_{database}"

    def _replication_slot_name(self, relation_id: int, database: str) -> str:
        return f"relation_{relation_id}_{database}"

    def _subscription_name(self, relation_id: int, database: str) -> str:
        return f"relation_{relation_id}_{database}"

    def _save_published_resources_info(
        self,
        relation_id: str,
        secret_id: str,
        publications: dict[str, dict[str, str | list[str]]],
    ) -> None:
        published_resources = json.loads(
            self.charm.app_peer_data.get("logical-replication-published-resources", "{}")
        )
        published_resources[relation_id] = {
            "secret-id": secret_id,
            "publications": publications,
        }
        self.charm.app_peer_data["logical-replication-published-resources"] = json.dumps(
            published_resources
        )

    def _subscriptions_info(self) -> dict[str, str]:
        for subscriptions_info in json.loads(
            self.charm.app_peer_data.get("logical-replication-subscriptions", "{}")
        ).values():
            return subscriptions_info
        return {}

    def _create_user(self, relation_id: int) -> tuple[str, str]:
        user = f"logical_replication_relation_{relation_id}"
        password = new_password()
        logger.info(
            f"Creating new user {user} for {LOGICAL_REPLICATION_OFFER_RELATION} #{relation_id}"
        )
        self.charm.postgresql.create_user(user, password, replication=True)
        return user, password

    def _get_secret(self, relation_id: int) -> Secret:
        """Returns logical replication secret. Updates, if content changed."""
        secret_label = f"{SECRET_LABEL}-{relation_id}"
        try:
            # Avoid recreating the secret.
            secret = self.charm.model.get_secret(label=secret_label)
            if not secret.id:
                # Workaround for the secret id not being set with model uuid.
                secret._id = f"secret://{self.model.uuid}/{secret.get_info().id.split(':')[1]}"
            return secret
        except SecretNotFoundError:
            logger.debug(
                f"Creating new secret for {LOGICAL_REPLICATION_OFFER_RELATION} #{relation_id}"
            )
        username, password = self._create_user(relation_id)
        return self.charm.model.app.add_secret(
            content={
                "primary": self.charm.primary_endpoint,
                "username": username,
                "password": password,
            },
            label=secret_label,
        )
