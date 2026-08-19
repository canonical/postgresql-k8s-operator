# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
"""TLS wiring tests for the single-kernel (lib) TLS handler.

The charm consumes the library TLS handler (single_kernel_postgresql.events.tls.TLS)
and TLS manager (single_kernel_postgresql.managers.tls.TLSManager) instead of its
own removed src/relations/tls.py. These tests exercise the lib-backed wiring:
state-backed cert storage via TLSManager, the charm's reload bridge that calls
update_config after the handler stores+pushes certificates, and the K8s-specific
CA artifacts (container trust store + the charm-local CA bundle) the charm still
owns because the lib does not write them.
"""

from unittest.mock import Mock, patch

import pytest
from ops.testing import Harness
from single_kernel_postgresql.config.literals import (
    PEER_RELATION,
    TLS_CA_BUNDLE_FILE,
)
from single_kernel_postgresql.events.tls import TLS
from single_kernel_postgresql.managers.tls import TLSManager

from charm import PostgresqlOperatorCharm


@pytest.fixture(autouse=True)
def harness():
    harness = Harness(PostgresqlOperatorCharm)
    peer_rel_id = harness.add_relation(PEER_RELATION, "postgresql-k8s")
    harness.add_relation_unit(peer_rel_id, "postgresql-k8s/0")
    harness.begin()
    yield harness
    harness.cleanup()


def test_tls_handler_is_lib_backed(harness):
    """The charm wires the lib TLS handler + manager (not the removed relations.tls)."""
    charm = harness.charm
    assert isinstance(charm.tls, TLS)
    assert isinstance(charm.tls_manager, TLSManager)
    # The handler owns the operator client/peer requirers and the refresh event.
    assert hasattr(charm.tls, "client_certificate")
    assert hasattr(charm.tls, "peer_certificate")
    assert hasattr(charm.tls, "refresh_tls_certificates_event")
    # The removed method must not resurface anywhere.
    assert not hasattr(charm, "push_tls_files_to_workload")


def test_is_tls_enabled_reflects_tls_manager(harness):
    """is_tls_enabled is driven by TLSManager.get_client_tls_files(), not the handler."""
    with patch("charm.TLSManager.get_client_tls_files") as _get_client_tls_files:
        _get_client_tls_files.return_value = (None, None, None)
        assert harness.charm.is_tls_enabled is False

        _get_client_tls_files.return_value = ("key", "ca", "cert")
        assert harness.charm.is_tls_enabled is True


def test_reload_bridge_observes_tls_files_pushed(harness):
    """The reload bridge fires on the lib's tls_files_pushed event, not certificate_available.

    The lib emits tls_files_pushed only after a successful push, so the sync+reload runs once
    the files are on disk; a deferred push never emits and never triggers a stale reload. This
    also removes the ops observer-order dependency the bridge previously relied on.
    """
    with (
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.PostgresqlOperatorCharm._sync_tls_trust_store_and_bundle") as _sync,
    ):
        harness.charm.tls.tls_files_pushed.emit()
        _sync.assert_called_once_with()
        _update_config.assert_called_once_with()


def test_sync_tls_trust_store_and_bundle_writes_ca_artifacts(harness, tmp_path):
    """Sync pushes the CA into the container trust store and writes the bundle.

    The K8s-specific sync pushes the operator CA into the container trust store
    and writes the charm-local CA bundle the Patroni REST client verifies against.
    """
    container = Mock()
    container.can_connect.return_value = True
    workload = Mock()
    workload.container = container
    bundle_path = tmp_path / TLS_CA_BUNDLE_FILE

    with (
        patch("charm.PostgresqlOperatorCharm.workload", new=workload),
        patch(
            "charm.TLSManager.get_client_tls_files",
            return_value=("key", "operator-ca", "cert"),
        ),
        patch("charm.TLSManager.get_peer_ca_bundle", return_value="bundle-content"),
        patch("charm.TLS_CA_BUNDLE_FILE", bundle_path.name),
        patch("builtins.open", create=True) as _open,
    ):
        harness.charm._sync_tls_trust_store_and_bundle()

        # CA pushed into the container trust store and refreshed.
        container.push.assert_called_once()
        pushed_args = container.push.call_args
        assert pushed_args.args[0].endswith("ca.crt")
        assert pushed_args.args[1] == "operator-ca"
        container.exec.assert_called_once_with(["update-ca-certificates"])
        # The charm-local CA bundle is written with the composed bundle content.
        _open.assert_called_once_with(f"/tmp/{bundle_path.name}", "w")
        _open.return_value.__enter__.return_value.write.assert_called_once_with("bundle-content")


def test_sync_tls_trust_store_skips_container_push_when_no_ca(harness):
    """Without a client CA, nothing is pushed into the container trust store."""
    container = Mock()
    container.can_connect.return_value = True
    workload = Mock()
    workload.container = container
    with (
        patch("charm.PostgresqlOperatorCharm.workload", new=workload),
        patch("charm.TLSManager.get_client_tls_files", return_value=(None, None, None)),
        patch("charm.TLSManager.get_peer_ca_bundle", return_value=""),
        patch("builtins.open", create=True) as _open,
    ):
        harness.charm._sync_tls_trust_store_and_bundle()
        container.push.assert_not_called()
        container.exec.assert_not_called()
        # The (empty) bundle is still written so the verify file exists.
        _open.assert_called_once()


def test_pebble_ready_internal_cert_path_calls_update_config(harness):
    """G3: pebble-ready internal-cert path calls update_config eagerly.

    On the pebble-ready bootstrap path, when the internal cert is generated +
    pushed + CA artifacts synced, update_config is called eagerly so Patroni
    config is rendered with ssl:on immediately (parity with the original charm's
    generate_internal_peer_cert -> push_tls_files_to_workload -> update_config).
    """
    with harness.hooks_disabled():
        harness.set_leader(True)
        harness.charm.set_secret("app", "internal-ca", "ca-content")

    event = Mock()
    event.workload.can_connect.return_value = True

    # get_secret: APP "internal-ca" present (truthy), UNIT "internal-cert" absent
    # (falsy) so the generate+push+sync block runs.
    secret_values = {"app": {"internal-ca": "ca-content"}, "unit": {}}

    def _get_secret(scope, key):
        return secret_values.get(scope, {}).get(key)

    # tls_transfer is an instance attribute (set in __init__), so patch its
    # method on the real instance rather than via charm.<Class>.tls_transfer.
    harness.charm.tls_transfer.get_ca_secret_names = Mock(return_value=[])

    with (
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.TLSManager.generate_internal_peer_cert") as _generate,
        patch("charm.TLSManager.push_tls_files") as _push,
        patch("charm.PostgresqlOperatorCharm._sync_tls_trust_store_and_bundle") as _sync,
        patch("charm.PostgresqlOperatorCharm._create_pgdata"),
        patch("charm.PostgresqlOperatorCharm._fix_pod"),
        patch("charm.PostgresqlOperatorCharm._update_pebble_layers"),
        patch("charm.PostgresqlOperatorCharm.get_secret", side_effect=_get_secret),
        patch("charm.PostgresqlOperatorCharm.push_ca_file_into_workload"),
    ):
        harness.charm._on_postgresql_pebble_ready(event)

    _generate.assert_called_once_with()
    _push.assert_called_once_with()
    _sync.assert_called_once_with()
    # The eager update_config on this path is the G3 parity restoration.
    _update_config.assert_called_once_with()


def test_reload_bridge_defers_when_update_config_raises(harness):
    """G4: _reload_tls_after_push defers when update_config raises.

    When update_config raises inside _reload_tls_after_push, the exception is
    caught, logged, and the event deferred (mirrors the original charm's
    push-failure defer) instead of propagating out of the observer.
    """
    with harness.hooks_disabled():
        harness.set_leader(True)
        harness.charm.set_secret("app", "internal-ca", "ca-content")

    event = Mock()
    with (
        patch(
            "charm.PostgresqlOperatorCharm.update_config",
            side_effect=RuntimeError("patroni render failed"),
        ),
        patch("charm.PostgresqlOperatorCharm._sync_tls_trust_store_and_bundle"),
    ):
        # Must not raise.
        harness.charm._reload_tls_after_push(event)

    event.defer.assert_called_once_with()


def test_reload_bridge_no_defer_on_success(harness):
    """G4 happy path: when update_config succeeds, the event is not deferred."""
    with harness.hooks_disabled():
        harness.set_leader(True)
        harness.charm.set_secret("app", "internal-ca", "ca-content")

    event = Mock()
    with (
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.PostgresqlOperatorCharm._sync_tls_trust_store_and_bundle"),
    ):
        harness.charm._reload_tls_after_push(event)

    _update_config.assert_called_once_with()
    event.defer.assert_not_called()
