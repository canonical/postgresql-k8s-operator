# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""In this class we manage certificates relation.

This class handles certificate request and renewal through
the interaction with the TLS Certificates Operator.

This library needs that https://charmhub.io/tls-certificates-interface/libraries/tls_certificates
library is imported to work.

It also needs the following methods in the charm class:
— get_hostname_by_unit: to retrieve the DNS hostname of the unit.
— get_secret: to retrieve TLS files from secrets.
— push_tls_files_to_workload: to push TLS files to the workload container and enable TLS.
— set_secret: to store TLS files as secrets.
— update_config: to disable TLS when relation with the TLS Certificates Operator is broken.
"""

import base64
import ipaddress
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
from ops.charm import ActionEvent
from ops.framework import Object
from ops.pebble import ConnectionError, PathError, ProtocolError

# The unique Charmhub library identifier, never change it
LIBID = "c27af44a92df4ef38d7ae06418b2800f"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version.
LIBPATCH = 6

logger = logging.getLogger(__name__)
SCOPE = "unit"
TLS_RELATION = "certificates"


class PostgreSQLTLS(Object):
    """In this class we manage certificates relation."""

    def __init__(
        self, charm, peer_relation: str, additional_dns_names: Optional[List[str]] = None
    ):
        """Manager of PostgreSQL relation with TLS Certificates Operator."""
        super().__init__(charm, "client-relations")
        self.charm = charm
        self.peer_relation = peer_relation
        self.additional_dns_names = additional_dns_names or []
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
        self._request_certificate(event.params.get("private-key", None))

    def _request_certificate(self, param: Optional[str]):
        """Request a certificate to TLS Certificates Operator."""
        if param is None:
            key = generate_private_key()
        else:
            key = self._parse_tls_file(param)

        csr = generate_csr(
            private_key=key,
            subject=self.charm.get_hostname_by_unit(self.charm.unit.name),
            additional_critical_extensions=self._get_tls_extensions(),
            **self._get_sans(),
        )

        self.charm.set_secret(SCOPE, "key", key.decode("utf-8"))
        self.charm.set_secret(SCOPE, "csr", csr.decode("utf-8"))

        if self.charm.model.get_relation(TLS_RELATION):
            self.certs.request_certificate_creation(certificate_signing_request=csr)

    @staticmethod
    def _parse_tls_file(raw_content: str) -> bytes:
        """Parse TLS files from both plain text or base64 format."""
        plain_text_tls_file_regex = r"(-+(BEGIN|END) [A-Z ]+-+)"
        if re.match(plain_text_tls_file_regex, raw_content):
            return re.sub(
                plain_text_tls_file_regex,
                "\\1",
                raw_content,
            ).encode("utf-8")
        return base64.b64decode(raw_content)

    def _on_tls_relation_joined(self, _) -> None:
        """Request certificate when TLS relation joined."""
        self._request_certificate(None)

    def _on_tls_relation_broken(self, _) -> None:
        """Disable TLS when TLS relation broken."""
        self.charm.set_secret(SCOPE, "ca", None)
        self.charm.set_secret(SCOPE, "cert", None)
        self.charm.set_secret(SCOPE, "chain", None)
        self.charm.update_config()

    def _on_certificate_available(self, event: CertificateAvailableEvent) -> None:
        """Enable TLS when TLS certificate available."""
        if (
            event.certificate_signing_request.strip()
            != str(self.charm.get_secret(SCOPE, "csr")).strip()
        ):
            logger.error("An unknown certificate available.")
            return

        self.charm.set_secret(
            SCOPE, "chain", "\n".join(event.chain) if event.chain is not None else None
        )
        self.charm.set_secret(SCOPE, "cert", event.certificate)
        self.charm.set_secret(SCOPE, "ca", event.ca)

        try:
            self.charm.push_tls_files_to_workload()
        except (ConnectionError, PathError, ProtocolError) as e:
            logger.error("Cannot push TLS certificates: %r", e)
            event.defer()
            return

    def _on_certificate_expiring(self, event: CertificateExpiringEvent) -> None:
        """Request the new certificate when old certificate is expiring."""
        if event.certificate.strip() != str(self.charm.get_secret(SCOPE, "cert")).strip():
            logger.error("An unknown certificate expiring.")
            return

        key = self.charm.get_secret(SCOPE, "key").encode("utf-8")
        old_csr = self.charm.get_secret(SCOPE, "csr").encode("utf-8")
        new_csr = generate_csr(
            private_key=key,
            subject=self.charm.get_hostname_by_unit(self.charm.unit.name),
            additional_critical_extensions=self._get_tls_extensions(),
            **self._get_sans(),
        )
        self.certs.request_certificate_renewal(
            old_certificate_signing_request=old_csr,
            new_certificate_signing_request=new_csr,
        )
        self.charm.set_secret(SCOPE, "csr", new_csr.decode("utf-8"))

    def _get_sans(self) -> dict:
        """Create a list of Subject Alternative Names for a PostgreSQL unit.

        Returns:
            A list representing the IP and hostnames of the PostgreSQL unit.
        """

        def is_ip_address(address: str) -> bool:
            """Returns whether and address is an IP address."""
            try:
                ipaddress.ip_address(address)
                return True
            except (ipaddress.AddressValueError, ValueError):
                return False

        unit_id = self.charm.unit.name.split("/")[1]

        # Create a list of all the Subject Alternative Names.
        sans = [
            f"{self.charm.app.name}-{unit_id}",
            self.charm.get_hostname_by_unit(self.charm.unit.name),
            socket.getfqdn(),
            str(self.charm.model.get_binding(self.peer_relation).network.bind_address),
        ]
        sans.extend(self.additional_dns_names)

        # Separate IP addresses and DNS names.
        sans_ip = [san for san in sans if is_ip_address(san)]
        # IP address need to be part of the DNS SANs list due to
        # https://github.com/pgbackrest/pgbackrest/issues/1977.
        sans_dns = sans

        return {
            "sans_ip": sans_ip,
            "sans_dns": sans_dns,
        }

    @staticmethod
    def _get_tls_extensions() -> Optional[List[ExtensionType]]:
        """Return a list of TLS extensions for which certificate key can be used."""
        basic_constraints = x509.BasicConstraints(ca=True, path_length=None)
        return [basic_constraints]

    def get_tls_files(self) -> (Optional[str], Optional[str], Optional[str]):
        """Prepare TLS files in special PostgreSQL way.

        PostgreSQL needs three files:
        — CA file should have a full chain.
        — Key file should have private key.
        — Certificate file should have certificate without certificate chain.
        """
        ca = self.charm.get_secret(SCOPE, "ca")
        chain = self.charm.get_secret(SCOPE, "chain")
        ca_file = chain if chain else ca

        key = self.charm.get_secret(SCOPE, "key")
        cert = self.charm.get_secret(SCOPE, "cert")
        return key, ca_file, cert
