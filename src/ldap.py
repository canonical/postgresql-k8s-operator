# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""LDAP implementation."""

import logging

from charms.glauth_k8s.v0.ldap import (
    LdapProviderData,
    LdapReadyEvent,
    LdapRequirer,
    LdapUnavailableEvent,
)
from charms.postgresql_k8s.v0.postgresql_tls import (
    TLS_TRANSFER_RELATION,
)
from ops import Relation
from ops.framework import Object
from ops.model import ActiveStatus, BlockedStatus

logger = logging.getLogger(__name__)


class PostgreSQLLDAP(Object):
    """In this class, we manage PostgreSQL LDAP access."""

    def __init__(self, charm, relation_name: str):
        """Manager of PostgreSQL LDAP."""
        super().__init__(charm, "ldap")
        self.charm = charm
        self.relation_name = relation_name

        # LDAP relation handles the config options for LDAP access
        self.ldap = LdapRequirer(self.charm, self.relation_name)
        self.framework.observe(self.ldap.on.ldap_ready, self._on_ldap_ready)
        self.framework.observe(self.ldap.on.ldap_unavailable, self._on_ldap_unavailable)

    @property
    def ca_transferred(self) -> bool:
        """Return whether the CA certificate has been transferred."""
        ca_transferred_relations = self.model.relations[TLS_TRANSFER_RELATION]

        for relation in ca_transferred_relations:
            if relation.app.name == self._relation.app.name:
                return True

        return False

    @property
    def _relation(self) -> Relation:
        """Return the relation object."""
        return self.model.get_relation(self.relation_name)

    def _on_ldap_ready(self, event: LdapReadyEvent) -> None:
        """Handler for the LDAP ready event."""
        if not self.ca_transferred:
            self.charm.unit.status = BlockedStatus("LDAP insecure. Send LDAP server certificate")
            event.defer()
            return

        logger.debug("Enabling LDAP connection")
        if self.charm.unit.is_leader():
            self.charm.app_peer_data.update({"ldap_enabled": "True"})

        self.charm.update_config()
        self.charm.unit.status = ActiveStatus()

    def _on_ldap_unavailable(self, _: LdapUnavailableEvent) -> None:
        """Handler for the LDAP unavailable event."""
        logger.debug("Disabling LDAP connection")
        if self.charm.unit.is_leader():
            self.charm.app_peer_data.update({"ldap_enabled": "False"})

        self.charm.update_config()

    def get_relation_data(self) -> LdapProviderData | None:
        """Get the LDAP info from the LDAP Provider class."""
        data = self.ldap.consume_ldap_relation_data(relation=self._relation)
        if data is None:
            logger.warning("LDAP relation is not ready")

        if not self.charm.is_connectivity_enabled:
            logger.warning("LDAP server will not be accessible")

        return data
