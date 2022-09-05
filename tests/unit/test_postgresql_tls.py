# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import base64
import socket
import unittest
from unittest.mock import MagicMock, call, patch

from ops.framework import EventBase
from ops.testing import Harness

from charm import PostgresqlOperatorCharm
from constants import PEER
from tests.helpers import patch_network_get

RELATION_NAME = "certificates"


class TestPostgreSQLTLS(unittest.TestCase):
    def delete_secrets(self) -> None:
        # Delete TLS secrets from the secret store.
        if self.charm.unit.is_leader():
            self.charm.set_secret("app", "ca", None)
            self.charm.set_secret("app", "cert", None)
            self.charm.set_secret("app", "chain", None)
        self.charm.set_secret("unit", "ca", None)
        self.charm.set_secret("unit", "cert", None)
        self.charm.set_secret("unit", "chain", None)

    def emit_certificate_available_event(self, internal: bool = False) -> None:
        scope = "internal" if internal else "external"
        self.charm.tls.certs.on.certificate_available.emit(
            certificate_signing_request=f"test-{scope}-csr",
            certificate=f"test-{scope}-cert",
            ca=f"test-{scope}-ca",
            chain=f"test-{scope}-chain",
        )

    def emit_certificate_expiring_event(self, internal: bool = False) -> None:
        scope = "internal" if internal else "external"
        self.charm.tls.certs.on.certificate_expiring.emit(
            certificate=f"test-{scope}-cert", expiry=None
        )

    @staticmethod
    def get_content_from_file(filename: str) -> str:
        with open(filename, "r") as file:
            content = file.read()
        return content

    def no_secrets(self, include_certificate: bool = True, include_internal: bool = False) -> bool:
        # Check whether there is no TLS secrets in the secret store.
        secrets = [self.charm.get_secret("unit", "ca"), self.charm.get_secret("unit", "chain")]
        if include_certificate:
            secrets.append(self.charm.get_secret("unit", "cert"))
        if include_internal:
            secrets.extend(
                [self.charm.get_secret("app", "ca"), self.charm.get_secret("app", "chain")]
            )
            if include_certificate:
                secrets.append(self.charm.get_secret("app", "cert"))
        return all(secret is None for secret in secrets)

    def relate_to_tls_certificates_operator(self) -> int:
        # Relate the charm to the TLS certificates operator.
        rel_id = self.harness.add_relation(RELATION_NAME, "tls-certificates-operator")
        self.harness.add_relation_unit(rel_id, "tls-certificates-operator/0")
        return rel_id

    def set_secrets(self) -> None:
        # Set some TLS secrets in the secret store.
        if self.charm.unit.is_leader():
            self.charm.set_secret("app", "ca", "test-internal-ca")
            self.charm.set_secret("app", "cert", "test-internal-cert")
            self.charm.set_secret("app", "chain", "test-internal-chain")
        self.charm.set_secret("unit", "ca", "test-external-ca")
        self.charm.set_secret("unit", "cert", "test-external-cert")
        self.charm.set_secret("unit", "chain", "test-external-chain")

    @patch_network_get(private_address="1.1.1.1")
    def setUp(self):
        self.harness = Harness(PostgresqlOperatorCharm)
        self.addCleanup(self.harness.cleanup)

        # Set up the initial relation and hooks.
        self.peer_rel_id = self.harness.add_relation(PEER, "postgresql-k8s")
        self.harness.add_relation_unit(self.peer_rel_id, "postgresql-k8s/0")
        self.harness.begin()
        self.charm = self.harness.charm

    @patch("charms.postgresql_k8s.v0.postgresql_tls.PostgreSQLTLS._request_certificate")
    @patch("charm.PostgresqlOperatorCharm._on_leader_elected")
    def test_on_set_tls_private_key(self, _, _request_certificate):
        # Create a mock event.
        mock_event = MagicMock(params={})

        # Test setting a TLS private key through a non leader unit
        # (only the external private key is set).
        self.harness.set_leader(False)
        self.charm.tls._on_set_tls_private_key(mock_event)
        _request_certificate.assert_called_once_with("unit", None)

        # Test setting a TLS private key through a leader unit
        # (both private keys are set).
        _request_certificate.reset_mock()
        self.harness.set_leader(True)
        self.charm.tls._on_set_tls_private_key(mock_event)
        calls = [call("unit", None), call("app", None)]
        _request_certificate.assert_has_calls(calls)

        # Test providing the private keys.
        mock_event.params["external-key"] = "test-external-key"
        mock_event.params["internal-key"] = "test-internal-key"
        _request_certificate.reset_mock()
        self.harness.set_leader(True)
        self.charm.tls._on_set_tls_private_key(mock_event)
        calls = [call("unit", "test-external-key"), call("app", "test-internal-key")]
        _request_certificate.assert_has_calls(calls)

    @patch_network_get(private_address="1.1.1.1")
    @patch(
        "charms.tls_certificates_interface.v1.tls_certificates.TLSCertificatesRequiresV1.request_certificate_creation"
    )
    def test_request_certificate(self, _request_certificate_creation):
        # Test without an established relation.
        self.delete_secrets()
        self.charm.tls._request_certificate("unit", None)
        self.assertIsNotNone(self.charm.get_secret("unit", "key"))
        self.assertIsNotNone(self.charm.get_secret("unit", "csr"))
        _request_certificate_creation.assert_not_called()

        # Test without providing a private key.
        with self.harness.hooks_disabled():
            self.relate_to_tls_certificates_operator()
        print(self.charm.tls._request_certificate("unit", None))
        self.assertIsNotNone(self.charm.get_secret("unit", "key"))
        self.assertIsNotNone(self.charm.get_secret("unit", "csr"))
        _request_certificate_creation.assert_called_once()

        # Test providing a private key.
        _request_certificate_creation.reset_mock()
        key = self.get_content_from_file(filename="tests/unit/key.pem")
        self.charm.tls._request_certificate("unit", key)
        self.assertIsNotNone(self.charm.get_secret("unit", "key"))
        self.assertIsNotNone(self.charm.get_secret("unit", "csr"))
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

    @patch_network_get(private_address="1.1.1.1")
    @patch("charms.postgresql_k8s.v0.postgresql_tls.PostgreSQLTLS._request_certificate")
    @patch("charm.PostgresqlOperatorCharm._on_leader_elected")
    def test_on_tls_relation_joined(self, _, _request_certificate):
        # Test event on a non leader unit.
        self.harness.set_leader(False)
        rel_id = self.relate_to_tls_certificates_operator()
        _request_certificate.assert_called_once_with("unit", None)

        # Test event on a leader unit.
        _request_certificate.reset_mock()
        with self.harness.hooks_disabled():
            self.harness.remove_relation(rel_id)
        self.harness.set_leader(True)
        self.relate_to_tls_certificates_operator()
        calls = [call("app", None), call("unit", None)]
        _request_certificate.assert_has_calls(calls)

    @patch_network_get(private_address="1.1.1.1")
    @patch.object(EventBase, "defer")
    @patch("charm.PostgresqlOperatorCharm.push_certificate_to_workload")
    @patch("charms.postgresql_k8s.v0.postgresql_tls.PostgreSQLTLS._request_certificate")
    @patch("charm.PostgresqlOperatorCharm._on_leader_elected")
    def test_on_tls_relation_broken(
        self, _, _request_certificate, _push_certificate_to_workload, _defer
    ):
        # Test event on a non leader unit.
        self.set_secrets()
        self.harness.set_leader(False)
        rel_id = self.relate_to_tls_certificates_operator()
        self.harness.remove_relation(rel_id)
        _push_certificate_to_workload.assert_called_once()
        self.assertTrue(self.no_secrets())

        # Test event again on a non leader unit, but with internal certificate.
        _push_certificate_to_workload.reset_mock()
        self.harness.set_leader(True)
        self.set_secrets()
        self.harness.set_leader(False)
        rel_id = self.relate_to_tls_certificates_operator()
        self.harness.remove_relation(rel_id)
        _defer.assert_called_once()
        _push_certificate_to_workload.assert_not_called()
        self.assertTrue(self.no_secrets())

        # Test event on a leader unit.
        _push_certificate_to_workload.reset_mock()
        self.harness.set_leader(True)
        rel_id = self.relate_to_tls_certificates_operator()
        self.harness.remove_relation(rel_id)
        _push_certificate_to_workload.assert_called_once()
        self.assertTrue(self.no_secrets(include_internal=True))

    @patch("charm.PostgresqlOperatorCharm.push_certificate_to_workload")
    @patch("charm.PostgresqlOperatorCharm._on_leader_elected")
    def test_on_certificate_available(self, _, _push_certificate_to_workload):
        # Test with no provided or invalid CSR.
        self.emit_certificate_available_event()
        self.assertTrue(self.no_secrets(include_internal=True))
        _push_certificate_to_workload.assert_not_called()

        # Test with internal CSR, but on a non leader unit.
        self.harness.set_leader(True)
        self.charm.set_secret("app", "csr", "test-internal-csr")
        self.harness.set_leader(False)
        self.emit_certificate_available_event(internal=True)
        self.assertTrue(self.no_secrets(include_internal=True))
        _push_certificate_to_workload.assert_not_called()

        # Test with internal CSR on a leader unit.
        self.harness.set_leader(True)
        self.emit_certificate_available_event(internal=True)
        self.assertEqual(self.charm.get_secret("app", "ca"), "test-internal-ca")
        self.assertEqual(self.charm.get_secret("app", "cert"), "test-internal-cert")
        self.assertEqual(self.charm.get_secret("app", "chain"), "test-internal-chain")
        _push_certificate_to_workload.assert_not_called()

        # Test with both internal and external CSR.
        self.charm.set_secret("unit", "csr", "test-external-csr")
        self.emit_certificate_available_event()
        _push_certificate_to_workload.assert_called_once()

    @patch_network_get(private_address="1.1.1.1")
    @patch(
        "charms.tls_certificates_interface.v1.tls_certificates.TLSCertificatesRequiresV1.request_certificate_renewal"
    )
    @patch("charm.PostgresqlOperatorCharm._on_leader_elected")
    def test_on_certificate_expiring(self, _, _request_certificate_renewal):
        # Test with no provided or invalid certificate.
        self.emit_certificate_expiring_event()
        self.assertTrue(self.no_secrets(include_internal=True))

        # Test with internal certificate, but on a non leader unit.
        self.harness.set_leader(True)
        self.charm.set_secret(
            "app", "key", self.get_content_from_file(filename="tests/unit/key.pem")
        )
        self.charm.set_secret("app", "cert", "test-internal-cert")
        self.charm.set_secret("app", "csr", "test-internal-csr")
        self.harness.set_leader(False)
        self.emit_certificate_expiring_event(internal=True)
        self.assertTrue(self.no_secrets(include_certificate=False, include_internal=True))

        # Test with internal certificate on a leader unit.
        self.harness.set_leader(True)
        self.emit_certificate_expiring_event(internal=True)
        self.assertNotEqual(self.charm.get_secret("app", "csr"), "test-internal-csr")
        _request_certificate_renewal.assert_called_once()

    @patch_network_get(private_address="1.1.1.1")
    def test_get_sans(self):
        sans = self.charm.tls._get_sans()
        self.assertEqual(sans, ["postgresql-k8s-0", socket.getfqdn(), "1.1.1.1"])

    def test_get_tls_extensions(self):
        # Test for external certificates.
        extensions = self.charm.tls._get_tls_extensions("unit")
        self.assertIsNone(extensions)

        # Test for internal certificates.
        extensions = self.charm.tls._get_tls_extensions("app")
        self.assertEqual(len(extensions), 1)
        self.assertEqual(extensions[0].ca, True)
        self.assertIsNone(extensions[0].path_length)

    def test_get_tls_files(self):
        # Test with no TLS files available.
        key, ca, certificate = self.charm.tls.get_tls_files("unit")
        self.assertIsNone(key)
        self.assertIsNone(ca)
        self.assertIsNone(certificate)

        # Test with TLS files available.
        self.charm.set_secret("unit", "key", "test-external-key")
        self.charm.set_secret("unit", "ca", "test-external-ca")
        self.charm.set_secret("unit", "cert", "test-external-cert")
        key, ca, certificate = self.charm.tls.get_tls_files("unit")
        self.assertEqual(key, "test-external-key")
        self.assertEqual(ca, "test-external-ca")
        self.assertEqual(certificate, "test-external-cert")
