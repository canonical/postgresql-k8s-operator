# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Service for synchronizing users from LDAP."""

import logging
import os
import time

from postgresql_ldap_sync.clients import DefaultPostgresClient, GLAuthClient
from postgresql_ldap_sync.matcher import DefaultMatcher

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


def main():
    """Main loop that gets the match."""
    ldap_client = _create_ldap_client()
    psql_client = _create_psql_client()
    matcher = _create_entity_matcher()

    while True:
        logger.info("Synchronizing LDAP users to PostgreSQL")

        matches = matcher.match_users(
            ldap_client.search_users(),
            psql_client.search_users(),
        )

        users_created = 0
        users_deleted = 0

        for match in matches:
            if match.should_create:
                psql_client.create_user(match.name)
                users_created += 1
            if match.should_delete:
                psql_client.delete_user(match.name)
                users_deleted += 1

        logger.info(f"Created {users_created} users and deleted {users_deleted} users")

        # Wait 30 seconds before executing the synchronizer again
        time.sleep(30)


if __name__ == "__main__":
    main()
