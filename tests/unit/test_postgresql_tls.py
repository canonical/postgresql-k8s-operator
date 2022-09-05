# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
import base64
import unittest
from unittest.mock import MagicMock, call, patch

from ops.testing import Harness

from charm import PostgresqlOperatorCharm
from constants import PEER
from tests.helpers import patch_network_get

RELATION_NAME = "certificates"


class TestPostgreSQLTLS(unittest.TestCase):
    @staticmethod
    def get_content_from_file(filename: str) -> str:
        with open(filename, "r") as file:
            certificate = file.read()
        return certificate

    @patch_network_get(private_address="1.1.1.1")
    def setUp(self):
        # self.harness = Harness(DatabaseCharm, actions=ACTIONS, meta=METADATA)
        self.harness = Harness(PostgresqlOperatorCharm)
        self.addCleanup(self.harness.cleanup)

        # Set up the initial relation and hooks.
        self.rel_id = self.harness.add_relation(RELATION_NAME, "tls-certificates-operator")
        self.harness.add_relation_unit(self.rel_id, "tls-certificates-operator/0")
        self.peer_rel_id = self.harness.add_relation(PEER, "database")
        self.harness.add_relation_unit(self.peer_rel_id, "database/0")
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
        # Test without providing a private key.
        self.charm.tls._request_certificate("unit", None)
        self.assertEqual(_set_secret.call_args_list[0][0][0], "unit")
        self.assertEqual(_set_secret.call_args_list[0][0][1], "key")
        self.assertEqual(_set_secret.call_args_list[1][0][0], "unit")
        self.assertEqual(_set_secret.call_args_list[1][0][1], "csr")
        _request_certificate_creation.assert_called_once()

        # Test providing a private key.
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
