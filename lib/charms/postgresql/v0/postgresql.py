# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""PostgreSQL library.

This [library](https://juju.is/docs/sdk/libraries) implements both sides of the
`PostgreSQL` [interface](https://juju.is/docs/sdk/relations).
The *provider* side of this interface is implemented by the
[PostgreSQL-k8s Charmed Operator](https://charmhub.io/postgresql-k8s)
and the [PostgreSQL Charmed Operator](https://charmhub.io/postgresql).
Any Charmed Operator that *requires* PostgreSQL for providing its
service should implement the *requirer* side of this interface.
In a nutshell using this library to implement a Charmed Operator *requiring*
PostgreSQL would look like
```
$ charmcraft fetch-lib charms.postgresql.v0.postgresql
```
`metadata.yaml`:
```
requires:
  database:
    interface: postgresql-client
```
`src/charm.py`:
```
from charms.postgresql.v0.postgresql import PostgreSQLEvents, PostgreSQLRequires
from ops.charm import CharmBase
class MyCharm(CharmBase):
    on = PostgreSQLEvents()
    def __init__(self, *args):
        super().__init__(*args)
        self.postgresql_requires = PostgreSQLRequires(self, "database")
        self.framework.observe(
            self.on.database_relation_joined,
            self._on_database_relation_joined,
        )
        self.framework.observe(
            self.on.database_available,
            self._on_database_available,
        )
        self.framework.observe(
            self.on.database_relation_broken,
            self._on_postgresql_broken,
        )
    def _on_database_relation_joined(self, event):
        # Request a database to PostgreSQL.
        self.postgresql_requires.set_database_name(event.relation, "test_database")
    def _on_database_available(self, event):
        # Get the connection strings for the primary instance and the replicas.
        endpoints = event.endpoints
        read_only_endpoints = event.read_only_endpoints
    def _on_postgresql_broken(self, event):
        # Stop service
        # ...
        self.unit.status = BlockedStatus("need PostgreSQL relation")
```
You can file bugs
[here](https://github.com/canonical/postgresql-k8s-operator/issues).
"""
import re
from typing import List

from ops.charm import CharmBase, CharmEvents, RelationEvent
from ops.framework import EventSource, Object

# The unique Charmhub library identifier, never change it
from ops.model import Relation

# The unique Charmhub library identifier, never change it
LIBID = "24ee217a54e840a598ff21a079c3e678"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1


PROXY_AUTH_USER_KEY = "auth-user"
PROXY_AUTH_PASSWORD_KEY = "auth-password"
PROXY_AUTH_QUERY_KEY = "auth-query"
DATABASE_NAME_KEY = "database"
ENDPOINTS_KEY = "endpoints"
READ_ONLY_ENDPOINTS_KEY = "read-only-endpoints"


class PostgreSQLRelationEvent(RelationEvent):
    """Base class for PostgreSQL library events."""

    @property
    def database(self) -> str:
        """Returns the database name that was requested by a consumer application."""
        return self._get_relation_data(DATABASE_NAME_KEY)

    def _get_relation_data(self, key: str) -> str:
        """Retrieves data from relation.

        Args:
            key: key to retrieve the data from the relation.

        Returns:
            value stored in the relation data bag for
                the specified key.
        """
        return (
            self.relation.data[self.relation.app].get(key)
            if self.relation and self.relation.app
            else None
        )

    @property
    def endpoints(self) -> str:
        """Returns the primary PostgreSQL instance connection string."""
        return self._get_relation_data(ENDPOINTS_KEY)

    @property
    def read_only_endpoints(self) -> str:
        """Returns a list of connections strings of the PostgreSQL replicas."""
        return self._get_relation_data(READ_ONLY_ENDPOINTS_KEY)

    @property
    def proxy_auth_user(self) -> str:
        """Get PostgreSQL proxy auth user."""
        return self._get_relation_data(PROXY_AUTH_USER_KEY)

    @property
    def proxy_auth_password(self) -> str:
        """Get PostgreSQL proxy auth password."""
        return self._get_relation_data(PROXY_AUTH_PASSWORD_KEY)

    @property
    def proxy_auth_query(self) -> str:
        """Get PostgreSQL proxy auth query."""
        return self._get_relation_data(PROXY_AUTH_QUERY_KEY)


class DatabaseRequestedEvent(PostgreSQLRelationEvent):
    """Event emitted when a new database is requested for use on this relation."""


class DatabaseAvailableEvent(PostgreSQLRelationEvent):
    """Event emitted when a new database is available for use on this relation."""


class ProxyAuthDetailsRequestedEvent(PostgreSQLRelationEvent):
    """Event emitted when the proxy auth details are requested."""


class ProxyAuthDetailsAvailableEvent(PostgreSQLRelationEvent):
    """Event emitted when the proxy auth details are available."""


class PostgreSQLEvents(CharmEvents):
    """PostgreSQL's events.

    This class defines the events that PostgreSQL can emit.
    Events:
        database_requested (DatabaseRequestedEvent)
        database_available (DatabaseAvailableEvent)
        proxy_auth_details_requested (ProxyAuthDetailsRequestedEvent)
        proxy_auth_details_available (ProxyAuthDetailsAvailableEvent)
    """

    database_requested = EventSource(DatabaseRequestedEvent)
    database_available = EventSource(DatabaseAvailableEvent)
    proxy_auth_details_requested = EventSource(ProxyAuthDetailsRequestedEvent)
    proxy_auth_details_available = EventSource(ProxyAuthDetailsAvailableEvent)


class InvalidDatabaseNameError(Exception):
    """Error to raise when an invalid database name is provided."""

    pass


class PostgreSQLRequires(Object):
    """Requires-side of the PostgreSQL relation."""

    def __init__(
        self, charm: CharmBase, database_relation_name: str, proxy_relation_name: str
    ) -> None:
        super().__init__(charm, [database_relation_name, proxy_relation_name])
        self.charm = charm

        if database_relation_name:
            self.framework.observe(
                charm.on[database_relation_name].relation_changed,
                self._on_database_relation_changed,
            )

        if proxy_relation_name:
            self.framework.observe(
                charm.on[proxy_relation_name].relation_changed, self._on_proxy_relation_changed
            )

    def _on_database_relation_changed(self, event: RelationEvent) -> None:
        """Emits and DatabaseAvailableEvent if all the database connection strings were set."""
        if event.relation.app and all(
            key in event.relation.data[event.relation.app]
            for key in (ENDPOINTS_KEY, READ_ONLY_ENDPOINTS_KEY)
        ):
            self.charm.on.database_available.emit(event.relation)

    def _on_proxy_relation_changed(self, event: RelationEvent) -> None:
        """Emits and ProxyAuthDetailsAvailableEvent if all the proxy auth details were set."""
        if event.relation.app and all(
            key in event.relation.data[event.relation.app]
            for key in (
                ENDPOINTS_KEY,
                READ_ONLY_ENDPOINTS_KEY,
                PROXY_AUTH_USER_KEY,
                PROXY_AUTH_PASSWORD_KEY,
                PROXY_AUTH_QUERY_KEY,
            )
        ):
            self.charm.on.proxy_auth_details_available.emit(event.relation)

    def set_database(self, relation: Relation, database_name: str) -> None:
        """Sets the database name to be created by the PostgreSQL charm.

        Raises:
            InvalidDatabaseNameError if the consumer applications
                provides an invalid database name.
        """
        if not re.fullmatch("[0-9a-zA-Z$_]+", database_name):
            raise InvalidDatabaseNameError()
        relation.data[self.charm.model.app][DATABASE_NAME_KEY] = database_name


class PostgreSQLProvides(Object):
    """Provides-side of the PostgreSQL relation."""

    def __init__(
        self, charm: CharmBase, database_relation_name: str, proxy_relation_name: str
    ) -> None:
        super().__init__(charm, [database_relation_name, proxy_relation_name])
        self.charm = charm

        if database_relation_name:
            self.framework.observe(
                charm.on[database_relation_name].relation_changed,
                self._on_database_relation_changed,
            )

        if proxy_relation_name:
            self.framework.observe(
                charm.on[proxy_relation_name].relation_changed, self._on_proxy_relation_changed
            )

    def _on_database_relation_changed(self, event: RelationEvent) -> None:
        """Event emitted when the database relation has changed."""
        # Validate that the expected data has changed to emit the custom event.
        if event.relation.app and DATABASE_NAME_KEY in event.relation.data[event.relation.app]:
            self.charm.on.database_requested.emit(event.relation)

    def _on_proxy_relation_changed(self, event: RelationEvent) -> None:
        """Event emitted when the proxy relation has changed."""
        # Validate that the expected data has changed to emit the custom event.
        if event.relation.app and DATABASE_NAME_KEY in event.relation.data[event.relation.app]:
            self.charm.on.proxy_auth_details_requested.emit(event.relation)

    def _update_relation_data(self, relation: Relation, key: str, value: str) -> None:
        """Set PostgreSQL primary connection string.

        This function writes in the application data bag, therefore,
        only the leader unit can call it.

        Args:
            relation: relation to write the data.
            key: key to write the date in the relation.
            value: value of the data to write in the relation.
        """
        relation.data[self.charm.model.app][key] = value

    def set_proxy_auth(
        self, relation: Relation, auth_user: str, auth_password: str, auth_query: str
    ) -> None:
        """Set PostgreSQL primary connection string.

        This function writes in the application data bag, therefore,
        only the leader unit can call it.

        Args:
            relation: relation to write the data.
            auth_user: user for proxy authentication.
            auth_password: password for proxy authentication.
            auth_query: authentication query to match incoming
                users/passwords on PostgreSQL database.
        """
        self._update_relation_data(relation, PROXY_AUTH_USER_KEY, auth_user)
        self._update_relation_data(relation, PROXY_AUTH_PASSWORD_KEY, auth_password)
        self._update_relation_data(relation, PROXY_AUTH_QUERY_KEY, auth_query)

    def set_endpoints(self, relation: Relation, connection_string: str) -> None:
        """Set PostgreSQL primary connection string.

        This function writes in the application data bag, therefore,
        only the leader unit can call it.

        Args:
            relation: relation to write the data.
            connection_string: PostgreSQL connection string.
        """
        self._update_relation_data(relation, ENDPOINTS_KEY, connection_string)

    def set_read_only_endpoints(self, relation: Relation, connection_strings: List[str]) -> None:
        """Set PostgreSQL replicas connection strings.

        This function writes in the application data bag, therefore,
        only the leader unit can call it.

        Args:
            relation: relation to write the data.
            connection_strings: PostgreSQL connections strings.
        """
        self._update_relation_data(relation, READ_ONLY_ENDPOINTS_KEY, ",".join(connection_strings))
