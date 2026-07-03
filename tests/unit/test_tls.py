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
    TLS_CLIENT_RELATION,
    TLS_PEER_RELATION,
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


def _observers_for(harness, bound_event):
    """Return method names observing the given bound event, in registration order."""
    emitter_path = bound_event.emitter.handle.path
    event_kind = bound_event.event_kind
    return [
        method
        for (_obs_path, method, e_path, e_kind) in harness.framework._observers
        if e_path == emitter_path and e_kind == event_kind
    ]


def test_reload_bridge_wired_after_handler_on_client_certificate(harness):
    """The reload bridge observes the same certificate_available event as the handler.

    The lib handler's store+push observer must be registered BEFORE the charm's
    reload bridge so that, when the event fires, certs are stored+pushed first and
    the reload (update_config) runs afterwards (ops calls observers in order).
    """
    methods = _observers_for(
        harness, harness.charm.tls.client_certificate.on.certificate_available
    )
    assert "_on_certificate_available" in methods, methods
    assert "_reload_tls_after_push" in methods, methods
    # Handler (store+push) before bridge (reload).
    assert methods.index("_on_certificate_available") < methods.index("_reload_tls_after_push")


def test_reload_bridge_wired_after_handler_on_peer_certificate(harness):
    """The reload bridge also observes the peer certificate_available event."""
    methods = _observers_for(harness, harness.charm.tls.peer_certificate.on.certificate_available)
    assert "_on_peer_certificate_available" in methods, methods
    assert "_reload_tls_after_push" in methods, methods
    assert methods.index("_on_peer_certificate_available") < methods.index(
        "_reload_tls_after_push"
    )


def test_reload_bridge_calls_update_config(harness):
    """_reload_tls_after_push syncs CA artifacts and reloads when internal-ca is present."""
    with harness.hooks_disabled():
        harness.set_leader(True)
        harness.charm.set_secret("app", "internal-ca", "ca-content")

    with (
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.PostgresqlOperatorCharm._sync_tls_trust_store_and_bundle") as _sync,
    ):
        harness.charm._reload_tls_after_push(Mock())
        _sync.assert_called_once_with()
        _update_config.assert_called_once_with()


def test_reload_bridge_skips_without_internal_ca(harness):
    """_reload_tls_after_push is a no-op when internal-ca is absent (defer path).

    The lib TLS handler defers its push when the internal CA isn't present yet
    (no files written to disk).  The bridge must not sync or reload in that case,
    or it would render ssl:on against TLS files that don't exist yet.
    """
    with (
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.PostgresqlOperatorCharm._sync_tls_trust_store_and_bundle") as _sync,
    ):
        harness.charm._reload_tls_after_push(Mock())
        _update_config.assert_not_called()
        _sync.assert_not_called()


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


def _relation_broken_observers(harness, relation_name):
    """Return method names observing relation_broken for the given relation, in order.

    Relation events are dispatched from the charm's ``on`` handle, so e_path is
    ``<CharmClass>/on`` and e_kind is ``<relation_name_underscored>_relation_broken``.
    """
    on_path = f"{harness.charm.handle.path}/on"
    event_kind = f"{relation_name.replace('-', '_')}_relation_broken"
    return [
        method
        for (_obs_path, method, e_path, e_kind) in harness.framework._observers
        if e_path == on_path and e_kind == event_kind
    ]


def test_reload_bridge_wired_on_client_relation_broken(harness):
    """On client-certificate relation_broken the lib handler fires before the charm bridge.

    When the TLS operator detaches, the lib clears + pushes TLS files (dropping the
    operator CA from state), then the bridge refreshes the /tmp bundle + reloads
    (update_config).  The lib's observer must come first so cleared state is visible
    when the bridge runs.
    """
    methods = _relation_broken_observers(harness, TLS_CLIENT_RELATION)
    assert "_on_certificate_available" in methods, (
        f"lib's _on_certificate_available not observing {TLS_CLIENT_RELATION} relation_broken: {methods}"
    )
    assert "_reload_tls_after_push" in methods, (
        f"charm's _reload_tls_after_push not observing {TLS_CLIENT_RELATION} relation_broken: {methods}"
    )
    assert methods.index("_on_certificate_available") < methods.index("_reload_tls_after_push"), (
        "lib handler must fire before charm bridge"
    )


def test_reload_bridge_wired_on_peer_relation_broken(harness):
    """On peer-certificate relation_broken the lib handler fires before the charm bridge.

    Same ordering constraint as the client case: the lib clears peer state first,
    the bridge sees the cleared state when computing the updated bundle.
    """
    methods = _relation_broken_observers(harness, TLS_PEER_RELATION)
    assert "_on_peer_certificate_available" in methods, (
        f"lib's _on_peer_certificate_available not observing {TLS_PEER_RELATION} relation_broken: {methods}"
    )
    assert "_reload_tls_after_push" in methods, (
        f"charm's _reload_tls_after_push not observing {TLS_PEER_RELATION} relation_broken: {methods}"
    )
    assert methods.index("_on_peer_certificate_available") < methods.index(
        "_reload_tls_after_push"
    ), "lib handler must fire before charm bridge"


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


def test_reload_bridge_defers_until_tls_files_on_disk(harness):
    """_reload_tls_after_push defers instead of rendering ssl:on before files exist.

    With TLS enabled (client cert material assigned) but the lib's file push not
    yet landed in the container (e.g. its Pebble push deferred), reloading would
    render ssl:on against missing files.  The bridge must defer and retry, and
    must not sync the trust store against not-yet-pushed material either.
    """
    with harness.hooks_disabled():
        harness.set_leader(True)
        harness.charm.set_secret("app", "internal-ca", "ca-content")

    event = Mock()
    with (
        patch("charm.TLSManager.get_client_tls_files", return_value=("key", "ca", "cert")),
        patch("charm.TLSManager.client_tls_files_on_disk", return_value=False),
        patch("charm.PostgresqlOperatorCharm.update_config") as _update_config,
        patch("charm.PostgresqlOperatorCharm._sync_tls_trust_store_and_bundle") as _sync,
    ):
        harness.charm._reload_tls_after_push(event)

    event.defer.assert_called_once_with()
    _update_config.assert_not_called()
    _sync.assert_not_called()
