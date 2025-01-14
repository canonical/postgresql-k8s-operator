# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""LDAP implementation."""

import logging
import os
from typing import Any

from charms.glauth_k8s.v0.ldap import (
    LdapProviderData,
    LdapReadyEvent,
    LdapRequirer,
    LdapUnavailableEvent,
)
from jinja2 import Template
from ops import Relation
from ops.framework import Object

from constants import (
    APP_SCOPE,
    MONITORING_PASSWORD_KEY,
    REPLICATION_PASSWORD_KEY,
    REWIND_PASSWORD_KEY,
    USER_PASSWORD_KEY,
)

logger = logging.getLogger(__name__)


class PostgreSQLLDAP(Object):
    """In this class, we manage PostgreSQL LDAP access."""

    def __init__(self, charm, relation_name: str):
        """Manager of PostgreSQL LDAP."""
        super().__init__(charm, "ldap")
        self.charm = charm
        self.relation_name = relation_name
        self.templates_path = f"{os.environ.get('CHARM_DIR')}/templates/ldap"

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

    def _render_template(self, file_name: str, values: dict[str, Any]) -> None:
        """Write a Jinja2 template file populated with the provided values.

        Args:
            file_name: name of the Jinja2 template file
            values: dictionary of key-value to populate
        """
        with open(f"{self.templates_path}/{file_name}.j2") as file:
            template = Template(file.read())

        contents = template.render(**values)

        with open(f"{self.charm._storage_path}/{file_name}", "w+") as file:
            file.write(contents)

    def render_initial_groups_file(self) -> None:
        """Render the initial set of LDAP groups to create."""
        template_name = "groups.ldif"
        template_values = {}

        self._render_template(template_name, template_values)

    def render_initial_users_file(self) -> None:
        """Render the initial set of LDAP users to create."""
        template_name = "users.ldif"
        template_values = {
            "monitoring_password": self.charm.get_secret(APP_SCOPE, MONITORING_PASSWORD_KEY),
            "replication_password": self.charm.get_secret(APP_SCOPE, REPLICATION_PASSWORD_KEY),
            "rewind_password": self.charm.get_secret(APP_SCOPE, REWIND_PASSWORD_KEY),
            "operator_password": self.charm.get_secret(APP_SCOPE, USER_PASSWORD_KEY),
        }

        self._render_template(template_name, template_values)

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
