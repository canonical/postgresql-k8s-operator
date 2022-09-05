# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""In this class we manage client database relations.

This class creates user and database for each application relation
and expose needed information for client connection via fields in
external relation.
"""

import base64
import logging
import re
import socket
from typing import List, Optional

from charms.tls_certificates_interface.v1.tls_certificates import (
    CertificateAvailableEvent,
    CertificateExpiringEvent,
    TLSCertificatesRequiresV1,
    generate_csr,
    generate_private_key,
)
from cryptography import x509
from cryptography.x509.extensions import ExtensionType
from ops.charm import ActionEvent, RelationBrokenEvent, RelationJoinedEvent
from ops.framework import Object
from ops.pebble import PathError, ProtocolError

# The unique Charmhub library identifier, never change it
LIBID = ""

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version.
LIBPATCH = 0

logger = logging.getLogger(__name__)
TLS_RELATION = "certificates"


class PostgreSQLTLS(Object):
    """In this class we manage client database relations."""

    def __init__(self, charm, peer_relation):
        """Manager of PostgreSQL client relations."""
        super().__init__(charm, "client-relations")
        self.charm = charm
        self.peer_relation = peer_relation
        self.certs = TLSCertificatesRequiresV1(self.charm, TLS_RELATION)
        self.framework.observe(
            self.charm.on.set_tls_private_key_action, self._on_set_tls_private_key
        )
        self.framework.observe(
            self.charm.on[TLS_RELATION].relation_joined, self._on_tls_relation_joined
        )
        self.framework.observe(
            self.charm.on[TLS_RELATION].relation_broken, self._on_tls_relation_broken
        )
        self.framework.observe(self.certs.on.certificate_available, self._on_certificate_available)
        self.framework.observe(self.certs.on.certificate_expiring, self._on_certificate_expiring)

    def _on_set_tls_private_key(self, event: ActionEvent) -> None:
        """Set the TLS private key, which will be used for requesting the certificate."""
        try:
            self._request_certificate("unit", event.params.get("external-key", None))
            if not self.charm.unit.is_leader():
                event.log(
                    "Only juju leader unit can set private key for the internal certificate. Skipping."
                )
                return
            self._request_certificate("app", event.params.get("internal-key", None))
        except ValueError as e:
            event.fail(str(e))

    def _request_certificate(self, scope: str, param: Optional[str]):
        if param is None:
            key = generate_private_key()
        else:
            key = self._parse_tls_file(param)

        csr = generate_csr(
            private_key=key,
            subject=self.charm.get_hostname_by_unit(self.charm.unit.name),
            sans=self._get_sans(),
            additional_critical_extensions=self._get_tls_extensions(scope),
        )

        self.charm.set_secret(scope, "key", key.decode("utf-8"))
        self.charm.set_secret(scope, "csr", csr.decode("utf-8"))

        if self.charm.model.get_relation(TLS_RELATION):
            self.certs.request_certificate_creation(certificate_signing_request=csr)

    @staticmethod
    def _parse_tls_file(raw_content: str) -> bytes:
        """Parse TLS files from both plain text or base64 format."""
        if re.match(r"(-+(BEGIN|END) [A-Z ]+-+)", raw_content):
            return re.sub(
                r"(-+(BEGIN|END) [A-Z ]+-+)",
                "\\1",
                raw_content,
            ).encode("utf-8")
        return base64.b64decode(raw_content)

    def _on_tls_relation_joined(self, _: RelationJoinedEvent) -> None:
        """Request certificate when TLS relation joined."""
        if self.charm.unit.is_leader():
            self._request_certificate("app", None)

        self._request_certificate("unit", None)

    def _on_tls_relation_broken(self, event: RelationBrokenEvent) -> None:
        """Disable TLS when TLS relation broken."""
        self.charm.set_secret("unit", "ca", None)
        self.charm.set_secret("unit", "cert", None)
        self.charm.set_secret("unit", "chain", None)
        if self.charm.unit.is_leader():
            self.charm.set_secret("app", "ca", None)
            self.charm.set_secret("app", "cert", None)
            self.charm.set_secret("app", "chain", None)
        if self.charm.get_secret("app", "cert"):
            logger.debug(
                "Defer till the leader delete the internal TLS certificate to avoid second reload."
            )
            event.defer()
            return
        try:
            self.charm.push_certificate_to_workload()
        except (PathError, ProtocolError) as e:
            logger.error("Cannot push TLS certificates: %r", e)
            event.defer()
            return

    def _on_certificate_available(self, event: CertificateAvailableEvent) -> None:
        """Enable TLS when TLS certificate available."""
        if event.certificate_signing_request == self.charm.get_secret("unit", "csr"):
            logger.debug("The external TLS certificate available.")
            scope = "unit"  # external crs
        elif event.certificate_signing_request == self.charm.get_secret("app", "csr"):
            logger.debug("The internal TLS certificate available.")
            if not self.charm.unit.is_leader():
                return
            scope = "app"  # internal crs
        else:
            logger.error("An unknown certificate expiring.")
            return

        old_cert = self.charm.get_secret(scope, "cert")
        renewal = old_cert and old_cert != event.certificate
        self.charm.set_secret(scope, "chain", event.chain)
        self.charm.set_secret(scope, "cert", event.certificate)
        self.charm.set_secret(scope, "ca", event.ca)

        if (
            not renewal
            and not self.charm.get_secret("app", "cert")
            or not self.charm.get_secret("unit", "cert")
        ):
            logger.debug(
                "Defer till both internal and external TLS certificates available to avoid second reload."
            )
            event.defer()
            return

        try:
            self.charm.push_certificate_to_workload()
        except (PathError, ProtocolError) as e:
            logger.error("Cannot push TLS certificates: %r", e)
            event.defer()
            return

    def _on_certificate_expiring(self, event: CertificateExpiringEvent) -> None:
        """Request the new certificate when old certificate is expiring."""
        if event.certificate == self.charm.get_secret("unit", "cert"):
            logger.debug("The external TLS certificate expiring.")
            scope = "unit"  # external cert
        elif event.certificate == self.charm.get_secret("app", "cert"):
            logger.debug("The internal TLS certificate expiring.")
            if not self.charm.unit.is_leader():
                return
            scope = "app"  # internal cert
        else:
            logger.error("An unknown certificate expiring.")
            return

        key = self.charm.get_secret(scope, "key").encode("utf-8")
        old_csr = self.charm.get_secret(scope, "csr").encode("utf-8")
        new_csr = generate_csr(
            private_key=key,
            subject=self.charm.get_hostname_by_unit(self.charm.unit.name),
            sans=self._get_sans(),
            additional_critical_extensions=self._get_tls_extensions(scope),
        )
        self.certs.request_certificate_renewal(
            old_certificate_signing_request=old_csr,
            new_certificate_signing_request=new_csr,
        )
        self.charm.set_secret(scope, "csr", new_csr.decode("utf-8"))

    def _get_sans(self) -> List[str]:
        """Create a list of DNS names for a PostgreSQL unit.

        Returns:
            A list representing the hostnames of the PostgreSQL unit.
        """
        unit_id = self.charm.unit.name.split("/")[1]
        return [
            f"{self.charm.app.name}-{unit_id}",
            socket.getfqdn(),
            str(self.charm.model.get_binding(self.peer_relation).network.bind_address),
        ]

    @staticmethod
    def _get_tls_extensions(scope: str) -> Optional[List[ExtensionType]]:
        """Return a list of TLS extensions for which certificate key can be used."""
        if scope != "app":
            return None

        basic_constraints = x509.BasicConstraints(ca=True, path_length=None)
        return [basic_constraints]

    def get_tls_files(self, scope: str) -> (Optional[str], Optional[str]):
        """Prepare TLS files in special PostgreSQL way.

        PostgreSQL needs two files:
        — CA file should have a full chain.
        — PEM file should have private key and certificate without certificate chain.
        """
        ca = self.charm.get_secret(scope, "ca")
        chain = self.charm.get_secret(scope, "chain")
        ca_file = chain if chain else ca

        key = self.charm.get_secret(scope, "key")
        cert = self.charm.get_secret(scope, "cert")
        return key, ca_file, cert
