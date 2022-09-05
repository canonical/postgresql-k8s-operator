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
    @staticmethod
    def get_content_from_file(filename: str) -> str:
        with open(filename, "r") as file:
            content = file.read()
        return content

    def no_secrets(self, include_internal: bool = False) -> bool:
        secrets = [
            self.charm.get_secret("unit", "ca"),
            self.charm.get_secret("unit", "cert"),
            self.charm.get_secret("unit", "chain"),
        ]
        if include_internal:
            secrets.extend(
                [
                    self.charm.get_secret("app", "ca"),
                    self.charm.get_secret("app", "cert"),
                    self.charm.get_secret("app", "chain"),
                ]
            )
        return all(secret is None for secret in secrets)

    def relate_to_tls_certificates_operator(self) -> int:
        rel_id = self.harness.add_relation(RELATION_NAME, "tls-certificates-operator")
        self.harness.add_relation_unit(rel_id, "tls-certificates-operator/0")
        return rel_id

    def set_secrets(self) -> None:
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
    @patch("charm.PostgresqlOperatorCharm.set_secret")
    def test_request_certificate(self, _set_secret, _request_certificate_creation):
        # Test without an established relation.
        self.charm.tls._request_certificate("unit", None)
        self.assertEqual(_set_secret.call_args_list[0][0][0], "unit")
        self.assertEqual(_set_secret.call_args_list[0][0][1], "key")
        self.assertEqual(_set_secret.call_args_list[1][0][0], "unit")
        self.assertEqual(_set_secret.call_args_list[1][0][1], "csr")
        _request_certificate_creation.assert_not_called()

        # Test without providing a private key.
        _set_secret.reset_mock()
        with self.harness.hooks_disabled():
            self.relate_to_tls_certificates_operator()
        self.charm.tls._request_certificate("unit", None)
        self.assertEqual(_set_secret.call_args_list[0][0][0], "unit")
        self.assertEqual(_set_secret.call_args_list[0][0][1], "key")
        self.assertEqual(_set_secret.call_args_list[1][0][0], "unit")
        self.assertEqual(_set_secret.call_args_list[1][0][1], "csr")
        _request_certificate_creation.assert_called_once()

        # Test providing a private key.
        _set_secret.reset_mock()
        _request_certificate_creation.reset_mock()
        key = self.get_content_from_file(filename="tests/unit/key.pem")
        self.charm.tls._request_certificate("unit", key)
        self.assertEqual(_set_secret.call_args_list[0][0][0], "unit")
        self.assertEqual(_set_secret.call_args_list[0][0][1], "key")
        self.assertEqual(_set_secret.call_args_list[1][0][0], "unit")
        self.assertEqual(_set_secret.call_args_list[1][0][1], "csr")
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

    def test_on_certificate_available(self):
        pass

    def test_on_certificate_expiring(self):
        pass

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
        key, certificate = self.charm.tls.get_tls_files("unit")
        self.assertIsNone(key)
        self.assertIsNone(certificate)

        # Test with TLS files available.
        self.charm.set_secret("unit", "key", "test-internal-key")
        self.charm.set_secret("unit", "cert", "test-internal-cert")
        key, certificate = self.charm.tls.get_tls_files("unit")
        self.assertEqual(key, "test-internal-key")
        self.assertEqual(certificate, "test-internal-cert")
