# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Service for synchronizing users from LDAP."""

import json
import logging
import os
import time
from typing import (
    Iterable,
    Iterator,
)

from postgresql_ldap_sync.clients import BasePostgreClient, DefaultPostgresClient, GLAuthClient
from postgresql_ldap_sync.matcher import DefaultMatcher
from postgresql_ldap_sync.models import UserMatch

logger = logging.getLogger(__name__)


def _create_entity_matcher() -> DefaultMatcher:
    return DefaultMatcher()


def _create_ldap_client() -> GLAuthClient:
    return GLAuthClient(
        host=os.environ["LDAP_HOST"],
        port=os.environ["LDAP_PORT"],
        base_dn=os.environ["LDAP_BASE_DN"],
        bind_username=os.environ["LDAP_BIND_USERNAME"],
        bind_password=os.environ["LDAP_BIND_PASSWORD"],
    )


def _create_psql_client() -> DefaultPostgresClient:
    return DefaultPostgresClient(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ["POSTGRES_PORT"],
        database=os.environ["POSTGRES_DATABASE"],
        username=os.environ["POSTGRES_USERNAME"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def _deserialize_role_mapping_rules() -> list[tuple[str]]:
    return json.loads(os.environ["LDAP_MAPPING_RULES"])


def _deserialize_role_mapping_filters() -> list[str]:
    return json.loads(os.environ["LDAP_MAPPING_FILTERS"])


def _filter_mapping_rules(mapping_rules: list[tuple], mapping_filters: list[str]) -> list[tuple]:
    """Filter invalid mapping rules due to the usage of forbidden groups."""
    right_rules = [(r, g) for r, g in mapping_rules if g not in mapping_filters]
    wrong_rules = [(r, g) for r, g in mapping_rules if g in mapping_filters]

    for _, group in wrong_rules:
        logger.warning(f"Tried to assign users to forbidden group: {group}")

    return right_rules


def _sync_users(psql_client: DefaultPostgresClient, matches: Iterator[UserMatch]) -> None:
    """Synchronize LDAP users to PostgreSQL."""
    users_created = 0
    users_deleted = 0

    for match in matches:
        if match.should_create:
            psql_client.create_user(match.name)
            users_created += 1
        if match.should_delete:
            psql_client.delete_user(match.name)
            users_deleted += 1

    logger.info(f"Created {users_created} users")
    logger.info(f"Deleted {users_deleted} users")


def _apply_roles(
    psql_client: BasePostgreClient,
    psql_users: Iterable[str],
    psql_groups: Iterable[str],
    mapping_rules: list[tuple],
) -> None:
    """Apply the mapped roles to every user matching a mapping rule regex."""
    memberships_updated = 0

    # Necessary conversion from potential iterator to list
    psql_users = list(psql_users)
    psql_groups = list(psql_groups)

    for regex, group in mapping_rules:
        users = [user for user in psql_users if regex.match(user)]

        psql_client.revoke_group_memberships(psql_groups, users)
        psql_client.grant_group_memberships([group], users)
        memberships_updated += len(users)

    logger.info(f"Updated {memberships_updated} group memberships")


def main():
    """Main loop that gets the match."""
    ldap_client = _create_ldap_client()
    psql_client = _create_psql_client()
    matcher = _create_entity_matcher()

    roles_map_rules = _deserialize_role_mapping_rules()
    roles_map_filters = _deserialize_role_mapping_filters()
    roles_map_rules = _filter_mapping_rules(roles_map_rules, roles_map_filters)

    while True:
        logger.info("Fetching users from PostgreSQL and LDAP")
        ldap_users = ldap_client.search_users()
        psql_users = psql_client.search_users()
        matched_users = matcher.match_users(ldap_users, psql_users)

        logger.info("Fetching groups from PostgreSQL")
        psql_groups = psql_client.search_groups()
        psql_groups = (group for group in psql_groups if group not in roles_map_filters)

        logger.info("Synchronizing LDAP users to PostgreSQL")
        _sync_users(psql_client, matched_users)
        _apply_roles(psql_client, psql_users, psql_groups, roles_map_rules)

        # Wait 30 seconds before executing the synchronizer again
        time.sleep(30)


if __name__ == "__main__":
    main()
