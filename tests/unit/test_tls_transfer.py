# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
from unittest.mock import patch

import pytest
from ops.pebble import ConnectionError as PebbleConnectionError
from ops.testing import Harness

from charm import PostgresqlOperatorCharm
from constants import PEER
from relations.tls_transfer import TLS_TRANSFER_RELATION

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


def relate_to_ca_certificates_operator(_harness):
    # Relate the charm to the send CA certificates operator.
    rel_id = _harness.add_relation(TLS_TRANSFER_RELATION, "ca-certificates-operator")
    _harness.add_relation_unit(rel_id, "ca-certificates-operator/0")
    return rel_id


def emit_ca_certificate_added_event(_harness, relation_id: int):
    _harness.charm.tls_transfer.certs_transfer.on.certificate_available.emit(
        relation_id=relation_id,
        certificate="test-cert",
        ca="test-ca",
        chain=["test-chain-ca-certificate", "test-chain-certificate"],
    )


def emit_ca_certificate_removed_event(_harness, relation_id: int):
    _harness.charm.tls_transfer.certs_transfer.on.certificate_removed.emit(
        relation_id=relation_id,
    )


def test_on_ca_certificate_added(harness):
    with (
        patch("ops.framework.EventBase.defer") as _defer,
        patch(
            "charm.PostgresqlOperatorCharm.push_ca_file_into_workload"
        ) as _push_ca_file_into_workload,
    ):
        rel_id = relate_to_ca_certificates_operator(harness)

        emit_ca_certificate_added_event(harness, rel_id)
        _push_ca_file_into_workload.assert_called_once()
        _defer.assert_not_called()

        _push_ca_file_into_workload.reset_mock()
        _push_ca_file_into_workload.side_effect = PebbleConnectionError

        emit_ca_certificate_added_event(harness, rel_id)
        _push_ca_file_into_workload.assert_called_once()
        _defer.assert_called_once()


def test_on_ca_certificate_removed(harness):
    with (
        patch("ops.framework.EventBase.defer") as _defer,
        patch(
            "charm.PostgresqlOperatorCharm.clean_ca_file_from_workload"
        ) as _clean_ca_file_from_workload,
    ):
        rel_id = relate_to_ca_certificates_operator(harness)

        emit_ca_certificate_removed_event(harness, rel_id)
        _clean_ca_file_from_workload.assert_called_once()
        _defer.assert_not_called()

        _clean_ca_file_from_workload.reset_mock()
        _clean_ca_file_from_workload.side_effect = PebbleConnectionError

        emit_ca_certificate_removed_event(harness, rel_id)
        _clean_ca_file_from_workload.assert_called_once()
        _defer.assert_called_once()


def test_on_ca_certificate_removed_relation_app_none(harness):
    with patch(
        "charm.PostgresqlOperatorCharm.clean_ca_file_from_workload"
    ) as _clean_ca_file_from_workload:
        rel_id = relate_to_ca_certificates_operator(harness)

        # The workload cleanup fails (e.g. Patroni rejects the request with 401),
        # so the event is deferred.
        _clean_ca_file_from_workload.return_value = False
        deferred_notices = len(list(harness.framework._storage.notices()))
        emit_ca_certificate_removed_event(harness, rel_id)
        assert len(list(harness.framework._storage.notices())) == deferred_notices + 1

        # The relation is already gone when the deferred event is re-emitted,
        # e.g. during the stop hook
        # (https://github.com/canonical/postgresql-k8s-operator/issues/1405).
        # In real Juju the remote application is not reported for a torn-down
        # relation, unlike the test backend which keeps its metadata.
        # Success is restored before remove_relation so the removal-triggered
        # event (the lib re-emits certificate_removed on relation-broken) does
        # not defer a second notice.
        _clean_ca_file_from_workload.return_value = True
        harness.remove_relation(rel_id)
        _clean_ca_file_from_workload.reset_mock()

        with patch.object(
            harness.charm.model._backend, "relation_remote_app_name", return_value=None
        ):
            harness.framework.reemit()

        # The cleanup is skipped since the remote application is not available
        # anymore, and the deferred event is consumed instead of crashing with
        # AttributeError: 'NoneType' object has no attribute 'name'.
        _clean_ca_file_from_workload.assert_not_called()
        assert len(list(harness.framework._storage.notices())) == deferred_notices


def test_on_ca_certificate_added_relation_app_none(harness):
    with patch(
        "charm.PostgresqlOperatorCharm.push_ca_file_into_workload"
    ) as _push_ca_file_into_workload:
        rel_id = relate_to_ca_certificates_operator(harness)

        # The workload push fails, so the event is deferred.
        _push_ca_file_into_workload.return_value = False
        deferred_notices = len(list(harness.framework._storage.notices()))
        emit_ca_certificate_added_event(harness, rel_id)
        assert len(list(harness.framework._storage.notices())) == deferred_notices + 1

        # The relation is already gone when the deferred event is re-emitted,
        # e.g. during the stop hook
        # (https://github.com/canonical/postgresql-k8s-operator/issues/1405).
        # In real Juju the remote application is not reported for a torn-down
        # relation, unlike the test backend which keeps its metadata.
        # Success is restored before remove_relation so the removal-triggered
        # event (the lib re-emits certificate_removed on relation-broken) does
        # not defer a second notice.
        _push_ca_file_into_workload.return_value = True
        harness.remove_relation(rel_id)
        _push_ca_file_into_workload.reset_mock()

        with patch.object(
            harness.charm.model._backend, "relation_remote_app_name", return_value=None
        ):
            harness.framework.reemit()

        # The push is skipped since the remote application is not available
        # anymore, and the deferred event is consumed instead of crashing with
        # AttributeError: 'NoneType' object has no attribute 'name'.
        _push_ca_file_into_workload.assert_not_called()
        assert len(list(harness.framework._storage.notices())) == deferred_notices
