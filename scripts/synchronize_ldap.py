# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Service for synchronizing users from LDAP."""

import atexit
import json
import logging
import os
import time

from postgresql_ldap_sync.clients import (
    BaseLDAPClient,
    BasePostgreClient,
    DefaultPostgresClient,
    GLAuthClient,
)
from postgresql_ldap_sync.matcher import DefaultMatcher

logger = logging.getLogger(__name__)


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


def _deserialize_group_mappings() -> list[tuple[str]]:
    return json.loads(os.environ["LDAP_GROUP_MAPPINGS"])


def _deserialize_group_identity() -> str:
    return json.loads(os.environ["LDAP_GROUP_IDENTITY"])


def _sync_users(
    ldap_client: BaseLDAPClient,
    psql_client: BasePostgreClient,
    group_mappings: list[tuple],
    group_identity: str,
) -> None:
    """Synchronize LDAP users to PostgreSQL."""
    ldap_users_groups = [ldap_group for ldap_group, _ in group_mappings]
    ldap_users = ldap_client.search_users(from_groups=ldap_users_groups)
    psql_users = psql_client.search_users(from_group=group_identity)

    roles_matcher = DefaultMatcher()
    users_created = 0
    users_deleted = 0

    for match in roles_matcher.match_users(ldap_users, psql_users):
        if match.should_keep:
            logger.debug(f"Ignoring LDAP user '{match.name}'")
        if match.should_create:
            logger.debug(f"Creating LDAP user '{match.name}' into PostgreSQL")
            psql_client.create_user(match.name)
            psql_client.grant_group_memberships([group_identity], [match.name])
            users_created += 1
        if match.should_delete:
            logger.debug(f"Deleting LDAP user '{match.name}' from PostgreSQL")
            psql_client.revoke_group_memberships([group_identity], [match.name])
            psql_client.delete_user(match.name)
            users_deleted += 1

    logger.info(f"Created {users_created} users")
    logger.info(f"Deleted {users_deleted} users")


def _sync_members(
    ldap_client: BaseLDAPClient,
    psql_client: BasePostgreClient,
    group_mappings: list[tuple],
    group_identity: str,
) -> None:
    """Synchronize LDAP memberships to PostgreSQL."""
    psql_groups = psql_client.search_groups()
    psql_groups = list(psql_groups)
    psql_groups.remove(group_identity)

    memberships_updated = 0

    for ldap_group, psql_group in group_mappings:
        ldap_users = ldap_client.search_users(from_groups=[ldap_group])
        ldap_users = list(ldap_users)

        logger.debug(f"Mapping LDAP group '{ldap_group}' within PostgreSQL")
        psql_client.revoke_group_memberships(psql_groups, ldap_users)
        psql_client.grant_group_memberships([psql_group], ldap_users)
        memberships_updated += len(ldap_users)

    logger.info(f"Updated {memberships_updated} group memberships")


def main():
    """Main loop that gets the match."""
    ldap_client = _create_ldap_client()
    psql_client = _create_psql_client()

    atexit.register(psql_client.close)

    group_mappings = _deserialize_group_mappings()
    group_identity = _deserialize_group_identity()

    while True:
        logger.info("Synchronizing LDAP users to PostgreSQL")
        _sync_users(ldap_client, psql_client, group_mappings, group_identity)
        _sync_members(ldap_client, psql_client, group_mappings, group_identity)

        # Wait 30 seconds before executing the synchronizer again
        time.sleep(30)


if __name__ == "__main__":
    main()
