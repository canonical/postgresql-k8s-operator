# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import base64
import socket
import unittest
from unittest.mock import MagicMock, patch

from ops.testing import Harness

from charm import PostgresqlOperatorCharm
from constants import PEER
from tests.helpers import patch_network_get

RELATION_NAME = "certificates"
SCOPE = "unit"


class TestPostgreSQLTLS(unittest.TestCase):
    def delete_secrets(self) -> None:
        # Delete TLS secrets from the secret store.
        self.charm.set_secret(SCOPE, "ca", None)
        self.charm.set_secret(SCOPE, "cert", None)
        self.charm.set_secret(SCOPE, "chain", None)

    def emit_certificate_available_event(self) -> None:
        self.charm.tls.certs.on.certificate_available.emit(
            certificate_signing_request="test-csr",
            certificate="test-cert",
            ca="test-ca",
            chain="test-chain",
        )

    def emit_certificate_expiring_event(self) -> None:
        self.charm.tls.certs.on.certificate_expiring.emit(certificate="test-cert", expiry=None)

    @staticmethod
    def get_content_from_file(filename: str) -> str:
        with open(filename, "r") as file:
            content = file.read()
        return content

    def no_secrets(self, include_certificate: bool = True) -> bool:
        # Check whether there is no TLS secrets in the secret store.
        secrets = [self.charm.get_secret(SCOPE, "ca"), self.charm.get_secret(SCOPE, "chain")]
        if include_certificate:
            secrets.append(self.charm.get_secret(SCOPE, "cert"))
        return all(secret is None for secret in secrets)

    def relate_to_tls_certificates_operator(self) -> int:
        # Relate the charm to the TLS certificates operator.
        rel_id = self.harness.add_relation(RELATION_NAME, "tls-certificates-operator")
        self.harness.add_relation_unit(rel_id, "tls-certificates-operator/0")
        return rel_id

    def set_secrets(self) -> None:
        # Set some TLS secrets in the secret store.
        self.charm.set_secret(SCOPE, "ca", "test-ca")
        self.charm.set_secret(SCOPE, "cert", "test-cert")
        self.charm.set_secret(SCOPE, "chain", "test-chain")

    def setUp(self):
        self.harness = Harness(PostgresqlOperatorCharm)
        self.addCleanup(self.harness.cleanup)

        # Set up the initial relation and hooks.
        self.peer_rel_id = self.harness.add_relation(PEER, "postgresql-k8s")
        self.harness.add_relation_unit(self.peer_rel_id, "postgresql-k8s/0")
        self.harness.begin()
        self.charm = self.harness.charm

    @patch("charms.postgresql_k8s.v0.postgresql_tls.PostgreSQLTLS._request_certificate")
    def test_on_set_tls_private_key(self, _request_certificate):
        # Create a mock event.
        mock_event = MagicMock(params={})

        # Test without providing a private key.
        self.charm.tls._on_set_tls_private_key(mock_event)
        _request_certificate.assert_called_once_with(None)

        # Test providing the private key.
        mock_event.params["external-key"] = "test-key"
        _request_certificate.reset_mock()
        self.charm.tls._on_set_tls_private_key(mock_event)
        _request_certificate.assert_called_once_with("test-key")

    @patch_network_get(private_address="1.1.1.1")
    @patch(
        "charms.tls_certificates_interface.v1.tls_certificates.TLSCertificatesRequiresV1.request_certificate_creation"
    )
    def test_request_certificate(self, _request_certificate_creation):
        # Test without an established relation.
        self.delete_secrets()
        self.charm.tls._request_certificate(None)
        self.assertIsNotNone(self.charm.get_secret(SCOPE, "key"))
        self.assertIsNotNone(self.charm.get_secret(SCOPE, "csr"))
        _request_certificate_creation.assert_not_called()

        # Test without providing a private key.
        with self.harness.hooks_disabled():
            self.relate_to_tls_certificates_operator()
        self.charm.tls._request_certificate(None)
        self.assertIsNotNone(self.charm.get_secret(SCOPE, "key"))
        self.assertIsNotNone(self.charm.get_secret(SCOPE, "csr"))
        _request_certificate_creation.assert_called_once()

        # Test providing a private key.
        _request_certificate_creation.reset_mock()
        key = self.get_content_from_file(filename="tests/unit/key.pem")
        self.charm.tls._request_certificate(key)
        self.assertIsNotNone(self.charm.get_secret(SCOPE, "key"))
        self.assertIsNotNone(self.charm.get_secret(SCOPE, "csr"))
        _request_certificate_creation.assert_called_once()

    def test_parse_tls_file(self):
        # Test with a plain text key.
        key = self.get_content_from_file(filename="tests/unit/key.pem")
        parsed_key = self.charm.tls._parse_tls_file(key)
        self.assertEqual(parsed_key, key.encode("utf-8"))

        # Test with a base4 encoded key.
        key = self.get_content_from_file(filename="tests/unit/key.pem")
        parsed_key = self.charm.tls._parse_tls_file(
            base64.b64encode(key.encode("utf-8")).decode("utf-8")
        )
        self.assertEqual(parsed_key, key.encode("utf-8"))

    @patch("charms.postgresql_k8s.v0.postgresql_tls.PostgreSQLTLS._request_certificate")
    def test_on_tls_relation_joined(self, _request_certificate):
        self.relate_to_tls_certificates_operator()
        _request_certificate.assert_called_once_with(None)

    @patch_network_get(private_address="1.1.1.1")
    @patch("charm.PostgresqlOperatorCharm.update_config")
    def test_on_tls_relation_broken(self, _update_config):
        _update_config.reset_mock()
        rel_id = self.relate_to_tls_certificates_operator()
        self.harness.remove_relation(rel_id)
        _update_config.assert_called_once()
        self.assertTrue(self.no_secrets())

    @patch("charm.PostgresqlOperatorCharm.push_tls_files_to_workload")
    def test_on_certificate_available(self, _push_tls_files_to_workload):
        # Test with no provided or invalid CSR.
        self.emit_certificate_available_event()
        self.assertTrue(self.no_secrets())
        _push_tls_files_to_workload.assert_not_called()

        # Test providing CSR.
        self.charm.set_secret(SCOPE, "csr", "test-csr")
        self.emit_certificate_available_event()
        self.assertEqual(self.charm.get_secret(SCOPE, "ca"), "test-ca")
        self.assertEqual(self.charm.get_secret(SCOPE, "cert"), "test-cert")
        self.assertEqual(self.charm.get_secret(SCOPE, "chain"), "test-chain")
        _push_tls_files_to_workload.assert_called_once()

    @patch_network_get(private_address="1.1.1.1")
    @patch(
        "charms.tls_certificates_interface.v1.tls_certificates.TLSCertificatesRequiresV1.request_certificate_renewal"
    )
    def test_on_certificate_expiring(self, _request_certificate_renewal):
        # Test with no provided or invalid certificate.
        self.emit_certificate_expiring_event()
        self.assertTrue(self.no_secrets())

        # Test providing a certificate.
        self.charm.set_secret(
            SCOPE, "key", self.get_content_from_file(filename="tests/unit/key.pem")
        )
        self.charm.set_secret(SCOPE, "cert", "test-cert")
        self.charm.set_secret(SCOPE, "csr", "test-csr")
        self.emit_certificate_expiring_event()
        self.assertTrue(self.no_secrets(include_certificate=False))
        _request_certificate_renewal.assert_called_once()

    @patch_network_get(private_address="1.1.1.1")
    def test_get_sans(self):
        sans = self.charm.tls._get_sans()
        self.assertEqual(sans, ["postgresql-k8s-0", socket.getfqdn(), "1.1.1.1"])

    def test_get_tls_extensions(self):
        extensions = self.charm.tls._get_tls_extensions()
        self.assertEqual(len(extensions), 1)
        self.assertEqual(extensions[0].ca, True)
        self.assertIsNone(extensions[0].path_length)

    def test_get_tls_files(self):
        # Test with no TLS files available.
        key, ca, certificate = self.charm.tls.get_tls_files()
        self.assertIsNone(key)
        self.assertIsNone(ca)
        self.assertIsNone(certificate)

        # Test with TLS files available.
        self.charm.set_secret(SCOPE, "key", "test-key")
        self.charm.set_secret(SCOPE, "ca", "test-ca")
        self.charm.set_secret(SCOPE, "cert", "test-cert")
        key, ca, certificate = self.charm.tls.get_tls_files()
        self.assertEqual(key, "test-key")
        self.assertEqual(ca, "test-ca")
        self.assertEqual(certificate, "test-cert")
