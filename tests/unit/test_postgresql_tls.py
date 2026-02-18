# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import base64
import socket
from unittest.mock import MagicMock, call, patch

import pytest
from charms.postgresql_k8s.v0.postgresql_tls import TLS_CREATION_RELATION
from ops.pebble import ConnectionError as PebbleConnectionError
from ops.testing import Harness

from charm import PostgresqlOperatorCharm
from constants import PEER

SCOPE = "unit"


@pytest.fixture(autouse=True)
def harness():
    harness = Harness(PostgresqlOperatorCharm)

    # Set up the initial relation and hooks.
    peer_rel_id = harness.add_relation(PEER, "postgresql-k8s")
    harness.add_relation_unit(peer_rel_id, "postgresql-k8s/0")
    harness.begin()
    yield harness
    harness.cleanup()


def delete_secrets(_harness):
    # Delete TLS secrets from the secret store.
    _harness.charm.set_secret(SCOPE, "ca", None)
    _harness.charm.set_secret(SCOPE, "cert", None)
    _harness.charm.set_secret(SCOPE, "chain", None)


def emit_ca_certificate_added_event(_harness, relation_id: int):
    _harness.charm.tls.certs_transfer.on.certificate_available.emit(
        relation_id=relation_id,
        certificate="test-cert",
        ca="test-ca",
        chain=["test-chain-ca-certificate", "test-chain-certificate"],
    )


def emit_ca_certificate_removed_event(_harness, relation_id: int):
    _harness.charm.tls.certs_transfer.on.certificate_removed.emit(
        relation_id=relation_id,
    )


def emit_tls_certificate_available_event(_harness):
    _harness.charm.tls.certs_creation.on.certificate_available.emit(
        certificate_signing_request="test-csr",
        certificate="test-cert",
        ca="test-ca",
        chain=["test-chain-ca-certificate", "test-chain-certificate"],
    )


def emit_tls_certificate_expiring_event(_harness):
    _harness.charm.tls.certs_creation.on.certificate_expiring.emit(
        certificate="test-cert",
        expiry=None,
    )


def get_content_from_file(filename: str):
    with open(filename) as file:
        content = file.read()
    return content


def no_secrets(_harness, include_certificate: bool = True):
    # Check whether there is no TLS secrets in the secret store.
    secrets = [_harness.charm.get_secret(SCOPE, "ca"), _harness.charm.get_secret(SCOPE, "chain")]
    if include_certificate:
        secrets.append(_harness.charm.get_secret(SCOPE, "cert"))
    return all(secret is None for secret in secrets)


def relate_to_tls_certificates_operator(_harness):
    # Relate the charm to the TLS certificates operator.
    rel_id = _harness.add_relation(TLS_CREATION_RELATION, "tls-certificates-operator")
    _harness.add_relation_unit(rel_id, "tls-certificates-operator/0")
    return rel_id


def test_on_set_tls_private_key(harness):
    with patch(
        "charms.postgresql_k8s.v0.postgresql_tls.PostgreSQLTLS._request_certificate"
    ) as _request_certificate:
        # Create a mock event.
        mock_event = MagicMock(params={})

        # Test without providing a private key.
        harness.charm.tls._on_set_tls_private_key(mock_event)
        _request_certificate.assert_called_once_with(None)

        # Test providing the private key.
        mock_event.params["private-key"] = "test-key"
        _request_certificate.reset_mock()
        harness.charm.tls._on_set_tls_private_key(mock_event)
        _request_certificate.assert_called_once_with("test-key")


def test_request_certificate(harness):
    with (
        patch(
            "charms.tls_certificates_interface.v2.tls_certificates.TLSCertificatesRequiresV2.request_certificate_creation"
        ) as _request_certificate_creation,
        patch(
            "charms.postgresql_k8s.v0.postgresql_tls.generate_csr",
            return_value=b"fake CSR",
        ) as _generate_csr,
        patch(
            "charms.postgresql_k8s.v0.postgresql_tls.generate_private_key",
            return_value=b"fake private key",
        ) as _generate_private_key,
    ):
        # Test without an established relation.
        delete_secrets(harness)
        harness.charm.tls._request_certificate(None)
        generate_csr_call = call(
            private_key=b"fake private key",
            subject="postgresql-k8s-0.postgresql-k8s-endpoints",
            sans_ip=["192.0.2.0"],
            sans_dns=[
                "postgresql-k8s-0",
                "postgresql-k8s-0.postgresql-k8s-endpoints",
                socket.getfqdn(),
                "192.0.2.0",
                f"postgresql-k8s-primary.{harness.charm.model.name}.svc.cluster.local",
                f"postgresql-k8s-replicas.{harness.charm.model.name}.svc.cluster.local",
            ],
        )
        _generate_csr.assert_has_calls([generate_csr_call])
        assert harness.charm.get_secret(SCOPE, "key") is not None
        assert harness.charm.get_secret(SCOPE, "csr") is not None
        _request_certificate_creation.assert_not_called()

        # Test without providing a private key.
        _generate_csr.reset_mock()
        with harness.hooks_disabled():
            relate_to_tls_certificates_operator(harness)
        harness.charm.tls._request_certificate(None)
        _generate_csr.assert_has_calls([generate_csr_call])
        assert harness.charm.get_secret(SCOPE, "key") is not None
        assert harness.charm.get_secret(SCOPE, "csr") is not None
        _request_certificate_creation.assert_called_once()

        # Test providing a private key.
        _generate_csr.reset_mock()
        _request_certificate_creation.reset_mock()
        key = get_content_from_file(filename="tests/unit/key.pem")
        harness.charm.tls._request_certificate(key)
        custom_key_generate_csr_call = call(
            private_key=key.encode("utf-8"),
            subject="postgresql-k8s-0.postgresql-k8s-endpoints",
            sans_ip=["192.0.2.0"],
            sans_dns=[
                "postgresql-k8s-0",
                "postgresql-k8s-0.postgresql-k8s-endpoints",
                socket.getfqdn(),
                "192.0.2.0",
                f"postgresql-k8s-primary.{harness.charm.model.name}.svc.cluster.local",
                f"postgresql-k8s-replicas.{harness.charm.model.name}.svc.cluster.local",
            ],
        )
        _generate_csr.assert_has_calls([custom_key_generate_csr_call])
        assert harness.charm.get_secret(SCOPE, "key") is not None
        assert harness.charm.get_secret(SCOPE, "csr") is not None
        _request_certificate_creation.assert_called_once()


def test_parse_tls_file(harness):
    # Test with a plain text key.
    key = get_content_from_file(filename="tests/unit/key.pem")
    parsed_key = harness.charm.tls._parse_tls_file(key)
    assert parsed_key == key.encode("utf-8")

    # Test with a base64 encoded key.
    key = get_content_from_file(filename="tests/unit/key.pem")
    parsed_key = harness.charm.tls._parse_tls_file(
        base64.b64encode(key.encode("utf-8")).decode("utf-8")
    )
    assert parsed_key == key.encode("utf-8")


def test_on_tls_relation_joined(harness):
    with patch(
        "charms.postgresql_k8s.v0.postgresql_tls.PostgreSQLTLS._request_certificate"
    ) as _request_certificate:
        relate_to_tls_certificates_operator(harness)
        _request_certificate.assert_called_once_with(None)


def test_on_tls_relation_broken(harness):
    with patch("charm.PostgresqlOperatorCharm.update_config") as _update_config:
        _update_config.reset_mock()
        rel_id = relate_to_tls_certificates_operator(harness)
        harness.remove_relation(rel_id)
        _update_config.assert_called_once()
        assert no_secrets(harness)


def test_on_tls_certificate_available(harness):
    with (
        patch("ops.framework.EventBase.defer") as _defer,
        patch(
            "charm.PostgresqlOperatorCharm.push_tls_files_to_workload"
        ) as _push_tls_files_to_workload,
    ):
        # Test with no provided or invalid CSR.
        emit_tls_certificate_available_event(harness)
        assert no_secrets(harness)
        _push_tls_files_to_workload.assert_not_called()

        # Test providing CSR.
        harness.charm.set_secret(SCOPE, "csr", "test-csr\n")
        emit_tls_certificate_available_event(harness)
        assert harness.charm.get_secret(SCOPE, "ca") == "test-ca"
        assert harness.charm.get_secret(SCOPE, "cert") == "test-cert"
        assert (
            harness.charm.get_secret(SCOPE, "chain")
            == "test-chain-ca-certificate\ntest-chain-certificate"
        )
        _push_tls_files_to_workload.assert_called_once()
        _defer.assert_not_called()

        _push_tls_files_to_workload.side_effect = PebbleConnectionError
        emit_tls_certificate_available_event(harness)
        _defer.assert_called_once()


def test_on_tls_certificate_expiring(harness):
    with (
        patch(
            "charms.tls_certificates_interface.v2.tls_certificates.TLSCertificatesRequiresV2.request_certificate_renewal"
        ) as _request_certificate_renewal,
    ):
        # Test with no provided or invalid certificate.
        emit_tls_certificate_expiring_event(harness)
        assert no_secrets(harness)

        # Test providing a certificate.
        harness.charm.set_secret(
            SCOPE, "key", get_content_from_file(filename="tests/unit/key.pem")
        )
        harness.charm.set_secret(SCOPE, "cert", "test-cert\n")
        harness.charm.set_secret(SCOPE, "csr", "test-csr")
        emit_tls_certificate_expiring_event(harness)
        assert no_secrets(harness, include_certificate=False)
        _request_certificate_renewal.assert_called_once()


def test_get_sans(harness):
    sans = harness.charm.tls._get_sans()
    assert sans == {
        "sans_ip": ["192.0.2.0"],
        "sans_dns": [
            "postgresql-k8s-0",
            "postgresql-k8s-0.postgresql-k8s-endpoints",
            socket.getfqdn(),
            "192.0.2.0",
            "postgresql-k8s-primary.None.svc.cluster.local",
            "postgresql-k8s-replicas.None.svc.cluster.local",
        ],
    }


def test_get_tls_files(harness):
    # Test with no TLS files available.
    key, ca, certificate = harness.charm.tls.get_tls_files()
    assert key is None
    assert ca is None
    assert certificate is None

    # Test with TLS files available.
    harness.charm.set_secret(SCOPE, "key", "test-key")
    harness.charm.set_secret(SCOPE, "ca", "test-ca")
    harness.charm.set_secret(SCOPE, "cert", "test-cert")
    key, ca, certificate = harness.charm.tls.get_tls_files()
    assert key == "test-key"
    assert ca == "test-ca"
    assert certificate == "test-cert"
