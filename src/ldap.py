# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""LDAP implementation."""

import logging
from typing import Any

from charms.glauth_k8s.v0.ldap import (
    LdapProviderData,
    LdapReadyEvent,
    LdapRequirer,
    LdapUnavailableEvent,
)
from ops import Relation
from ops.framework import Object

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

    @staticmethod
    def dict_to_hba_string(_dict: dict[str, Any]) -> str:
        """Transform a Python dictionary into a Host Based Authentication valid string."""
        for key, value in _dict.items():
            if isinstance(value, bool):
                _dict[key] = int(value)
            if isinstance(value, str):
                _dict[key] = f'"{value}"'

        return " ".join(f"{key}={value}" for key, value in _dict.items())

    @property
    def _relation(self) -> Relation:
        """Return the relation object."""
        return self.model.get_relation(self.relation_name)

    def _on_ldap_ready(self, _: LdapReadyEvent) -> None:
        """Reload the Patroni configuration when the LDAP service is available."""
        logger.debug("Enabling LDAP connection")
        if self.charm.unit.is_leader():
            self.charm.app_peer_data.update({"ldap_enabled": "True"})

        self.charm.update_config()

    def _on_ldap_unavailable(self, _: LdapUnavailableEvent) -> None:
        """Reload the Patroni configuration when the LDAP service is unavailable."""
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
