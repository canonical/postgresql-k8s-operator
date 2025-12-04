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

    def _on_relation_changed(self, event: RelationChangedEvent) -> None:
        if not self._relation_changed_checks(event):
            return

        errors = json.loads(event.relation.data[event.app].get("errors", "[]"))

        # Check if errors are stale by comparing with our current subscription-request
        if errors:
            our_request = json.loads(
                event.relation.data[self.model.app].get("subscription-request", "{}")
            )

            # If we have a subscription-request, re-validate to check if these errors are current
            # The _check_publisher_errors() will handle stale error detection
            if our_request and self.charm.unit.is_leader():
                logger.debug(
                    f"Publisher reported errors: {errors}. Re-validating to check if errors are current."
                )
                # Re-run validation which will properly check for stale errors
                if not self._validate_subscription_request():
                    # Validation failed with current errors
                    return
                # Validation passed, errors were stale - continue processing
                logger.info("Publisher errors were stale, continuing with relation processing")
                errors = []
            else:
                # No subscription-request yet, or not leader - process errors as-is
                for error in errors:
                    logger.error(
                        f"Got logical replication error from the publisher in {LOGICAL_REPLICATION_RELATION} #{event.relation.id}: {error}"
                    )
                    # Set specific message for circular replication errors
                    if "circular replication" in error.lower():
                        self.charm.set_unit_status(BlockedStatus("Circular replication detected"))
                    else:
                        self.charm.set_unit_status(
                            BlockedStatus(LOGICAL_REPLICATION_VALIDATION_ERROR_STATUS)
                        )
                return

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

    # endregion

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
        logger.info(
            f"[APPLY_CHANGED_CONFIG] Applying new config: "
            f"{self.charm.config.logical_replication_subscription_request}"
        )
        # Clear any previous error state when config changes
        # This prevents retry_validations() from validating stale config
        self.charm.app_peer_data["logical-replication-validation"] = "ongoing"

        # Send subscription request to publisher first, before full validation
        # This allows the publisher to detect circular replication and report errors
        # which we can then check before doing our local validation
        if relation := self.model.get_relation(LOGICAL_REPLICATION_RELATION):
            relation.data[self.model.app]["subscription-request"] = (
                self.charm.config.logical_replication_subscription_request or "{}"
            )

        if self._validate_subscription_request():
            self._apply_updated_subscription_request()
            # Clear any previous blocked status from validation errors
            self.charm._set_active_status()
        return True

    def retry_validations(self) -> None:
        """Run recurrent logical replication validation attempt.

        For subscribers - try to validate & apply subscription request.
        For publishers - try to validate & process all the offer relations.
        """
        if not self.charm.unit.is_leader() or not self.charm.primary_endpoint:
            return
        if self.charm.app_peer_data.get("logical-replication-validation") == "error":
            logger.info(
                f"[RETRY_VALIDATIONS] Found error state, retrying validation with config: "
                f"{self.charm.config.logical_replication_subscription_request}"
            )
            if self._validate_subscription_request():
                self._apply_updated_subscription_request()
                # Clear any previous blocked status from validation errors
                self.charm._set_active_status()
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

    def _is_error_relevant_to_request(
        self, error: str, subscription_request: dict[str, list[str]]
    ) -> bool:
        """Check if a publisher error is relevant to the current subscription request.

        Args:
            error: The error message from the publisher
            subscription_request: The subscription request being validated (database -> tables)

        Returns:
            True if the error is relevant to this request, False otherwise
        """
        # Non-circular errors apply to the whole request
        if "circular replication" not in error.lower():
            return True

        # For circular replication errors, check if they mention any of our tables
        for database, tables in subscription_request.items():
            for table in tables:
                # Check if this specific table is mentioned in the error
                if table in error and database in error:
                    return True

        return False

    def _check_publisher_errors(
        self, relation: Relation | None, subscription_request: dict[str, list[str]]
    ) -> bool:
        """Check if the publisher has reported errors for the current subscription request.

        Args:
            relation: The subscription relation
            subscription_request: The subscription request being validated (database -> tables)

        Returns:
            True if validation should fail, False to continue validation
        """
        if not relation:
            return False

        publisher_errors = json.loads(relation.data[relation.app].get("errors", "[]"))
        if not publisher_errors:
            return False

        # Check if we have the same subscription request in relation data
        # If the request has changed, old errors may not be relevant
        current_relation_request = json.loads(
            relation.data[self.model.app].get("subscription-request", "{}")
        )

        # If requests don't match, publisher errors are stale - ignore them
        # The publisher will re-validate when we update the subscription-request
        if current_relation_request != subscription_request:
            return False

        # Filter to only errors relevant to the tables we're trying to subscribe to
        relevant_errors = [
            error
            for error in publisher_errors
            if self._is_error_relevant_to_request(error, subscription_request)
        ]

        # Only fail if we have relevant errors
        if not relevant_errors:
            return False

        # Check if any relevant error mentions circular replication
        for error in relevant_errors:
            if "circular replication" in error.lower():
                self._fail_validation(
                    f"Publisher rejected subscription: {error}",
                    status_msg="Circular replication detected",
                )
                return True

        # Generic publisher error
        self._fail_validation(f"Publisher errors: {', '.join(relevant_errors)}")
        return True

    def _validate_table_for_subscription(
        self,
        relation: Relation | None,
        database: str,
        schematable: str,
        subscription_request_relation: dict[str, list[str]],
    ) -> bool:
        """Validate a single table for subscription.

        Args:
            relation: The subscription relation
            database: The database name
            schematable: The table name in schema.table format
            subscription_request_relation: Current subscription request from relation data

        Returns:
            True if validation passes, False otherwise
        """
        try:
            schema, table = schematable.split(".")
        except ValueError:
            return self._fail_validation(f"table format isn't right at {schematable}")

        if not self.charm.postgresql.table_exists(database, schema, table):
            return self._fail_validation(
                f"table {schematable} in database {database} doesn't exist"
            )

        # Check for circular replication FIRST before checking if table is empty
        # This is important because:
        # 1. If we're already publishing to the remote app, we can't subscribe from them
        # 2. The table might not be empty because of existing data (not from replication)
        if relation and self._check_subscriber_circular_replication(
            relation, database, schematable
        ):
            return self._fail_validation(
                f"circular replication detected for table {schematable} in database {database}",
                status_msg=f"Circular replication detected for table {schematable}",
            )

        # Also check replication chains (for multi-hop scenarios)
        if relation and self._would_create_circular_replication(relation, database, schematable):
            return self._fail_validation(
                f"circular replication detected for table {schematable} in database {database}",
                status_msg=f"Circular replication detected for table {schematable}",
            )

        already_subscribed = (
            database in subscription_request_relation
            and schematable in subscription_request_relation[database]
        )
        if not already_subscribed and not self.charm.postgresql.is_table_empty(
            database, schema, table
        ):
            return self._fail_validation(f"table {schematable} in database {database} isn't empty")

        return True

    def _validate_subscription_request(self) -> bool:
        try:
            subscription_request_config = json.loads(
                self.charm.config.logical_replication_subscription_request or "{}"
            )
            logger.info(
                f"[VALIDATION] Starting validation with config: {subscription_request_config}"
            )
        except json.JSONDecodeError as err:
            return self._fail_validation(f"JSON decode error {err}")

        relation = self.model.get_relation(LOGICAL_REPLICATION_RELATION)

        # Check for errors from the publisher first
        if self._check_publisher_errors(relation, subscription_request_config):
            return False

        subscription_request_relation = (
            json.loads(relation.data[self.model.app].get("subscription-request", "{}"))
            if relation
            else {}
        )

        for database, schematables in subscription_request_config.items():
            if not self.charm.postgresql.database_exists(database):
                return self._fail_validation(f"database {database} doesn't exist")
            for schematable in schematables:
                if not self._validate_table_for_subscription(
                    relation, database, schematable, subscription_request_relation
                ):
                    return False

        self.charm.app_peer_data["logical-replication-validation"] = ""
        return True

    def _fail_validation(self, message: str | None = None, status_msg: str | None = None) -> bool:
        if message:
            logger.error(f"Logical replication validation: {message}")
        self.charm.app_peer_data["logical-replication-validation"] = "error"
        blocked_message = status_msg or LOGICAL_REPLICATION_VALIDATION_ERROR_STATUS
        self.charm.set_unit_status(BlockedStatus(blocked_message))
        return False

    def _would_create_circular_replication(
        self, relation: Relation | None, database: str, table: str
    ) -> bool:
        """Check if subscribing to a table would create circular replication.

        This checks the replication chain in the remote publication to see if our app
        is already in the chain, which would mean the data originated from us.

        Args:
            relation: The logical-replication relation we're subscribing on
            database: The database name
            table: The table name (schema.table format)

        Returns:
            True if subscribing would create a circle, False otherwise
        """
        if not relation:
            return False

        # Get the publications from the remote app
        remote_publications = json.loads(relation.data[relation.app].get("publications", "{}"))

        if database not in remote_publications:
            return False

        # Get the replication chains for this database
        replication_chains = remote_publications[database].get("replication-chains", {})

        if table not in replication_chains:
            return False

        # Check if our app name is in the chain
        chain = replication_chains[table]
        if self.model.app.name in chain:
            logger.warning(
                f"Circular replication detected: table {table} in database {database} "
                f"has replication chain {chain} which includes this app ({self.model.app.name})"
            )
            return True

        return False

    def _check_subscriber_circular_replication(
        self, relation: Relation, database: str, table: str
    ) -> bool:
        """Check if we're already publishing this table to the remote app.

        This prevents circular replication where:
        - App A is publishing table X to App B (via offer relation)
        - App A tries to subscribe to table X from App B (via subscription relation)

        This check runs on the subscriber side during validation, before the
        subscription request is even sent to the publisher.

        Args:
            relation: The subscription relation we're trying to create
            database: The database name
            table: The table name (schema.table format)

        Returns:
            True if we're already publishing this table to the remote app
        """
        logger.info(
            f"[CIRCULAR_CHECK] Checking if we're publishing {table} in {database} "
            f"to {relation.app.name}"
        )
        # Get our offer relation (limit: 1, so only one relation possible)
        offer_relation = self.model.get_relation(LOGICAL_REPLICATION_OFFER_RELATION)

        if not offer_relation:
            # No offer relation, so we're not publishing anything
            logger.info("[CIRCULAR_CHECK] No offer relation found")
            return False

        # Check if the offer relation is to the same app we want to subscribe from
        if offer_relation.app.name != relation.app.name:
            logger.info(
                f"[CIRCULAR_CHECK] Offer relation is to {offer_relation.app.name}, "
                f"not {relation.app.name}"
            )
            return False

        # We have an offer relation to the same app! Check if we're publishing this table
        publications = json.loads(offer_relation.data[self.model.app].get("publications", "{}"))
        logger.info(f"[CIRCULAR_CHECK] Our publications: {publications}")

        if database not in publications:
            logger.info(f"[CIRCULAR_CHECK] Database {database} not in publications")
            return False

        # Check if the table is in our publications
        published_tables = publications[database].get("tables", [])
        logger.info(f"[CIRCULAR_CHECK] Published tables in {database}: {published_tables}")
        if table in published_tables:
            logger.warning(
                f"Circular replication detected: we are publishing {table} in {database} "
                f"to {offer_relation.app.name}, and trying to subscribe to the same table from them"
            )
            return True

        logger.info(f"[CIRCULAR_CHECK] Table {table} not in published tables")
        return False

    def _check_publisher_circular_replication(
        self, offer_relation: Relation, database: str, tables: list[str]
    ) -> list[str]:
        """Check if we (publisher) are subscribed to the requester.

        This prevents circular replication where:
        - Direct circular: App A is subscribed to App B for table X, and App B tries to
          subscribe to App A for the same table X
        - Multi-hop circular: App A -> B -> C -> A, where the chain eventually loops back

        The check works by examining:
        1. If we have an active subscription to the same app (direct circular)
        2. If we're subscribed to any table and the requester's app is in its replication
           chain (multi-hop circular)

        Args:
            offer_relation: The offer relation being processed
            database: The database being requested
            tables: List of tables being requested

        Returns:
            List of tables that would create circular replication (empty if none)
        """
        circular_tables = []

        # Get our subscription relation (limit: 1)
        subscription_relation = self.model.get_relation(LOGICAL_REPLICATION_RELATION)

        if not subscription_relation:
            # No subscription relation, can't have circular replication
            return circular_tables

        # Get the publications to see what tables we're actually subscribed to
        publications = json.loads(
            subscription_relation.data[subscription_relation.app].get("publications", "{}")
        )

        # Check for direct circular replication (we're subscribed to the same app)
        if subscription_relation.app.name == offer_relation.app.name:
            # We're subscribed to the same app that's trying to subscribe to us!
            # Check if we have active subscriptions to this database
            subscriptions = self._subscriptions_info()

            if database not in subscriptions:
                # We're subscribed to the same app but not this database, so no circular replication
                return circular_tables

            if database not in publications:
                # Subscription exists but publications not yet set up
                return circular_tables

            # Check for overlap in tables
            subscribed_tables = publications[database].get("tables", [])
            overlap = set(tables) & set(subscribed_tables)

            if overlap:
                circular_tables.extend(sorted(overlap))
                logger.warning(
                    f"Direct circular replication detected: subscribed to {subscription_relation.app.name} "
                    f"for tables {subscribed_tables}, and they are trying to subscribe to us "
                    f"for tables {tables}. Overlapping tables: {circular_tables}"
                )

            return circular_tables

        # Check for multi-hop circular replication
        # If we're subscribed to any table in this database, check if the requester's
        # app is in the replication chain for that table
        if database not in publications:
            # Not subscribed to this database, can't have multi-hop circular replication
            return circular_tables

        # Get replication chains from our subscription
        replication_chains = publications[database].get("replication-chains", {})

        for table in tables:
            if table not in replication_chains:
                # We're not subscribed to this table, so no circular replication for it
                continue

            # Check if the requester's app is in the replication chain
            chain = replication_chains[table]
            if offer_relation.app.name in chain:
                circular_tables.append(table)
                logger.warning(
                    f"Multi-hop circular replication detected: subscribed to {table} "
                    f"in {database} with chain {chain}, and {offer_relation.app.name} "
                    f"(which is in the chain) is trying to subscribe to us for the same table"
                )

        return sorted(circular_tables)

    def _build_replication_chains(self, database: str, tables: list[str]) -> dict[str, list[str]]:
        """Build replication chains for tables being published.

        This checks if we're subscribed to any of these tables. If so, we extend
        their replication chain. Otherwise, we're the origin.

        Args:
            database: The database name
            tables: List of tables being published

        Returns:
            Dictionary mapping table names to their replication chains
        """
        chains: dict[str, list[str]] = {}

        # Get our subscription relation (limit: 1, so only one relation possible)
        subscription_relation = self.model.get_relation(LOGICAL_REPLICATION_RELATION)

        if not subscription_relation:
            # No subscription, we're the origin for all tables
            for table in tables:
                chains[table] = [self.model.app.name]
            return chains

        # Get the remote publications we're subscribed to
        remote_publications = json.loads(
            subscription_relation.data[subscription_relation.app].get("publications", "{}")
        )

        if database not in remote_publications:
            # Not subscribed to this database, we're the origin
            for table in tables:
                chains[table] = [self.model.app.name]
            return chains

        # Get the replication chains from our subscription
        remote_chains = remote_publications[database].get("replication-chains", {})

        for table in tables:
            if table in remote_chains:
                # Extend the chain - we're republishing data we subscribed to
                chains[table] = remote_chains[table] + [self.model.app.name]
            else:
                # We're the origin for this table
                chains[table] = [self.model.app.name]

        return chains

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

    def _process_offer(self, relation: Relation) -> None:
        logger.debug(
            f"Started processing offer for {LOGICAL_REPLICATION_OFFER_RELATION} #{relation.id}"
        )

        subscriptions_request = json.loads(
            relation.data[relation.app].get("subscription-request", "{}")
        )
        publications = json.loads(relation.data[self.model.app].get("publications", "{}"))
        secret = self._get_secret(relation.id)
        user = secret.peek_content()["username"]
        errors = []

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
            # Check for circular replication on publisher side
            circular_tables = self._check_publisher_circular_replication(
                relation, database, tables
            )
            if circular_tables:
                error = (
                    f"circular replication detected for tables {', '.join(circular_tables)} "
                    f"in database {database}"
                )
                errors.append(error)
                logger.error(
                    f"Cannot create/update publication for "
                    f"{LOGICAL_REPLICATION_OFFER_RELATION} #{relation.id}: {error}"
                )
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
                    "replication-chains": self._build_replication_chains(database, tables),
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
                publications[database]["replication-chains"] = self._build_replication_chains(
                    database, tables
                )
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
