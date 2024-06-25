# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
from unittest.mock import Mock, patch

import pytest
from ops.testing import Harness

from charm import PostgresqlOperatorCharm
from constants import PEER


@pytest.fixture(autouse=True)
def harness():
    with patch("charm.KubernetesServicePatch", lambda x, y: None):
        # Mock generic sync client to avoid search to ~/.kube/config.
        patcher = patch("lightkube.core.client.GenericSyncClient")
        patcher.start()

        harness = Harness(PostgresqlOperatorCharm)

        # Set up the initial relation and hooks.
        peer_rel_id = harness.add_relation(PEER, "postgresql-k8s")
        harness.add_relation_unit(peer_rel_id, "postgresql-k8s/0")
        harness.begin()
        yield harness
        harness.cleanup()


def test_on_reenable_oversee_users(harness):
    # Fail if unit is not leader
    event = Mock()

    harness.charm.async_replication._on_reenable_oversee_users(event)

    event.fail.assert_called_once_with("Unit is not leader")
    event.fail.reset_mock()

    # Fail if peer data is not set
    with harness.hooks_disabled():
        harness.set_leader()

    harness.charm.async_replication._on_reenable_oversee_users(event)

    event.fail.assert_called_once_with("Oversee users is not suppressed")
    event.fail.reset_mock()

    with harness.hooks_disabled():
        harness.charm._peers.data[harness.charm.app].update({"suppress-oversee-users": "true"})

        harness.charm.async_replication._on_reenable_oversee_users(event)
        assert harness.charm._peers.data[harness.charm.app] == {}
