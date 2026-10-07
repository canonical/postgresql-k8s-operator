#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Regression test for DPE-10203.

After a dead-datacenter failover, re-establishing async replication to a fresh
cluster used to deadlock: the offer/primary and consumer/standby sides shared one
fixed Juju secret label, and a cluster that had been a standby kept that label
reserved as a consumer alias, so the later owner-create collided. The
force-removed cross-model relation delivers no ``relation-broken``, which also
leaves a stale ``promoted-cluster-counter`` behind.

This test kills the primary model by deleting its Kubernetes namespace (the
units and their storage die together, and no charm hook can run afterwards),
force-promotes the standby, clears the dead relation and offer with
``remove-relation --force`` / ``remove-saas --force`` (neither delivering
``relation-broken``), and runs ``create-replication`` against a fresh cluster —
which must succeed rather than deadlock. It asserts the labelless-secret
contract carried by the single kernel library's DPE-10203 fix: the owner owns
the shared secret with no label, and no side registers a consumer-side label
alias under the legacy name.

Unlike the VM counterpart, no PostgreSQL watcher charm is deployed: each cluster
uses 3 units so the cross-cluster Raft keeps an odd number of members without a
witness (a 4-member raft stalls standby formation).
"""

import logging
import shlex
import subprocess
import time
from collections.abc import Generator

import jubilant
import pytest
from jubilant import Juju
from tenacity import Retrying, stop_after_delay, wait_fixed

from .. import architecture
from ..helpers import KUBECTL, METADATA
from ..jubilant_helpers import retry_if_cli_error
from .high_availability_helpers_new import (
    consumer_alias_exists,
    get_app_leader,
    get_app_units,
    get_async_secret_labels,
    get_db_max_written_value,
    get_db_standby_leader_unit,
    start_continuous_writes,
    wait_for_apps_status,
)

DB_APP_1 = "db1"  # original primary model (killed mid-test)
DB_APP_2 = "db2"  # standby cluster, force-promoted to primary
DB_APP_3 = "db3"  # fresh model the recovered primary re-replicates to

# Each cluster also gets a client application so the regression covers real data:
# writes made through the pre-death primary must survive the force-promotion and
# re-appear on the fresh re-replication target (mirrors the original async
# replication tests, which relate the test app to every cluster).
DB_TEST_APP_NAME = "postgresql-test-app"
DB_TEST_APP_1 = "test-app1"
DB_TEST_APP_2 = "test-app2"
DB_TEST_APP_3 = "test-app3"
DB_NAME_1 = f"{DB_TEST_APP_1.replace('-', '_')}_database"

# The shared cluster-credentials secret is owned LABELLESS (DPE-10203): the owner
# references it by the id persisted in app peer data, the consumer purely by the id
# published in relation data. This is the legacy label the charm must never attach
# again — kept as a literal so this stays a black-box test.
FORBIDDEN_LABEL = "async-replication-secret"

MINUTE_SECS = 60

logging.getLogger("jubilant.wait").setLevel(logging.WARNING)


@pytest.fixture(scope="module")
def first_model(juju: Juju) -> Generator:
    """Return the first (original primary) model."""
    yield juju.model


def _extra_model(
    base_model: str, juju: Juju, request: pytest.FixtureRequest, suffix: str
) -> Generator:
    """Create a model derived from *base_model* and destroy it on teardown.

    The base model is passed in explicitly (not read from ``juju.model``) because
    jubilant's ``add_model`` repoints ``juju.model`` at the newly added model, so a
    second derived model would otherwise chain off the first one's name.
    """
    model_name = f"{base_model}-{suffix}"
    logging.info(f"Creating model: {model_name}")
    juju.add_model(model_name)
    yield model_name
    if request.config.getoption("--keep-models"):
        juju.model = base_model
        return
    logging.info(f"Destroying model: {model_name}")
    juju.destroy_model(model_name, destroy_storage=True, force=True)
    # destroy_model nulls the fixture's model when it matches the destroyed one;
    # restore the base model so later teardowns and assertions keep a usable handle.
    juju.model = base_model


@pytest.fixture(scope="module")
def second_model(first_model: str, juju: Juju, request: pytest.FixtureRequest) -> Generator:
    """Create and return the second (standby -> promoted primary) model."""
    yield from _extra_model(first_model, juju, request, "other")


@pytest.fixture(scope="module")
def third_model(first_model: str, juju: Juju, request: pytest.FixtureRequest) -> Generator:
    """Create and return the third (fresh re-replication target) model."""
    yield from _extra_model(first_model, juju, request, "third")


def _delete_namespace(namespace: str) -> None:
    """Delete a model's Kubernetes namespace, killing its units without any hook.

    A Juju model on Kubernetes maps to a single namespace named after the model;
    deleting it stops every unit's agent at once (the dead-datacenter condition)
    while the Juju controller, in its own namespace, keeps serving the surviving
    models. Charm hooks can no longer run on the dead side, so nothing observes
    the subsequent forced teardown — which is the point of the regression.
    """
    subprocess.run(
        [
            *shlex.split(KUBECTL),
            "delete",
            "namespace",
            namespace,
            "--wait=true",
            "--timeout=5m",
        ],
        check=True,
    )


def test_deploy(first_model: str, second_model: str, charm: str) -> None:
    """Deploy the two initial 3-unit PostgreSQL clusters, each with its own test app.

    The third (fresh re-replication target) cluster is deployed later, inside the
    dead-DC test, after the primary model has died: the Kubernetes charm runs in
    CI on a single-node cluster, and keeping the concurrent pod count down while
    the standby forms is what keeps the control plane alive.
    """
    configuration = {"profile": "testing"}
    constraints = {"arch": architecture.architecture}
    resources = {"postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"]}

    clusters = (
        (first_model, DB_APP_1, DB_TEST_APP_1),
        (second_model, DB_APP_2, DB_TEST_APP_2),
    )

    for model_name, app, test_app in clusters:
        model = Juju(model=model_name)
        model.deploy(
            charm=charm,
            app=app,
            base="ubuntu@24.04",
            config=configuration,
            constraints=constraints,
            resources=resources,
            num_units=3,
            trust=True,
        )
        model.deploy(
            charm=DB_TEST_APP_NAME,
            app=test_app,
            base="ubuntu@24.04",
            channel="latest/edge",
            constraints=constraints,
            num_units=1,
        )
        model.integrate(f"{test_app}:database", f"{app}:database")

    for model_name, app, test_app in clusters:
        model = Juju(model=model_name)
        retry_if_cli_error(
            lambda model=model, app=app, test_app=test_app: model.wait(
                ready=wait_for_apps_status(jubilant.all_active, app, test_app),
                timeout=25 * MINUTE_SECS,
            )
        )


def test_relate_and_replicate(first_model: str, second_model: str) -> None:
    """Make db2 a standby cluster of db1 via async replication."""
    model_1 = Juju(model=first_model)
    model_2 = Juju(model=second_model)

    logging.info("Creating the offer in the first model and consuming it in the second")
    retry_if_cli_error(
        lambda: model_1.offer(f"{first_model}.{DB_APP_1}", endpoint="replication-offer")
    )
    retry_if_cli_error(lambda: model_2.consume(f"{first_model}.{DB_APP_1}"))
    retry_if_cli_error(lambda: model_2.integrate(DB_APP_1, f"{DB_APP_2}:replication"))

    # Wait for the relation to settle before create-replication: the action fails
    # unless every unit has published its address in the relation data.
    retry_if_cli_error(
        lambda: model_1.wait(
            ready=wait_for_apps_status(jubilant.any_active, DB_APP_1), timeout=10 * MINUTE_SECS
        )
    )
    retry_if_cli_error(
        lambda: model_2.wait(
            ready=wait_for_apps_status(jubilant.any_active, DB_APP_2), timeout=10 * MINUTE_SECS
        )
    )

    logging.info("Running create replication action")
    retry_if_cli_error(
        lambda: model_1.run(
            unit=get_app_leader(model_1, DB_APP_1),
            action="create-replication",
            wait=5 * MINUTE_SECS,
        ).raise_on_failure()
    )

    retry_if_cli_error(
        lambda: model_1.wait(
            ready=wait_for_apps_status(jubilant.all_active, DB_APP_1), timeout=20 * MINUTE_SECS
        )
    )
    retry_if_cli_error(
        lambda: model_2.wait(
            ready=wait_for_apps_status(jubilant.all_active, DB_APP_2), timeout=20 * MINUTE_SECS
        )
    )

    # db1 owns the shared secret with NO label (the DPE-10203 contract), and db2
    # is now the read-only standby cluster.
    assert get_async_secret_labels(model_1, DB_APP_1) == set()
    assert get_db_standby_leader_unit(model_2, DB_APP_2)

    # Start client writes on the primary: the data they produce must survive the
    # dead-DC teardown and re-appear on the fresh re-replication target.
    start_continuous_writes(model_1, DB_TEST_APP_1)

    # Consumer side of the contract: the standby reached standby state by reading
    # the offer secret purely by id, so it registered NO consumer alias under any
    # label.
    assert not consumer_alias_exists(model_2, DB_APP_2, FORBIDDEN_LABEL), (
        "db2 registered a stale-prone consumer-side label alias"
    )


def test_dead_dc_failover_and_recreate_replication(
    first_model: str, second_model: str, third_model: str, charm: str
) -> None:
    """The DPE-10203 regression: dead-DC teardown must not deadlock create-replication."""
    model_2 = Juju(model=second_model)

    # 1. Stop the client writes, capture the last written value, and wait until async
    #    replication has caught up — the data written so far must survive everything
    #    that follows. Then kill the primary datacenter by deleting its model's
    #    Kubernetes namespace: db1's units and their storage die together, and none
    #    of their hooks can run again.
    model_1 = Juju(model=first_model)
    model_1.run(
        unit=get_app_leader(model_1, DB_TEST_APP_1),
        action="stop-continuous-writes",
        wait=2 * MINUTE_SECS,
    ).raise_on_failure()
    max_written = get_db_max_written_value(
        model_1, DB_APP_1, get_app_leader(model_1, DB_APP_1), DB_NAME_1
    )
    for attempt in Retrying(
        stop=stop_after_delay(5 * MINUTE_SECS), wait=wait_fixed(10), reraise=True
    ):
        with attempt:
            assert all(
                get_db_max_written_value(model_2, DB_APP_2, unit, DB_NAME_1) == max_written
                for unit in get_app_units(model_2, DB_APP_2)
            ), "async replication has not caught up with the pre-death writes"

    logging.info(f"Killing the primary DC by deleting the namespace of model {first_model}")
    _delete_namespace(first_model)
    time.sleep(30)  # let db2 observe the primary loss before forcing promotion

    # 2. Force-promote the standby (a graceful promote refuses with the primary gone).
    logging.info("Force-promoting the standby cluster to primary")
    retry_if_cli_error(
        lambda: model_2.run(
            unit=get_app_leader(model_2, DB_APP_2),
            action="promote-to-primary",
            params={"scope": "cluster", "force": True},
            wait=5 * MINUTE_SECS,
        ).raise_on_failure()
    )
    retry_if_cli_error(
        lambda: model_2.wait(
            ready=wait_for_apps_status(jubilant.all_active, DB_APP_2), timeout=20 * MINUTE_SECS
        )
    )
    # The pre-death data must have survived the force-promotion.
    assert all(
        get_db_max_written_value(model_2, DB_APP_2, unit, DB_NAME_1) == max_written
        for unit in get_app_units(model_2, DB_APP_2)
    ), "pre-death data did not survive the force-promotion"

    # 3. Issue 1 from the ticket: attack the dead relation with remove-relation
    #    --force, for which Juju delivers no events. While that limitation stands the
    #    offer survives it, so the ticket's workaround — remove-saas --force — runs
    #    next; if Juju ever honors the removal, the offer is already gone and the
    #    workaround's "not found" is tolerated (the consumer_alias_exists idiom).
    #    Either way no relation-broken reaches the charm, which is what leaves the
    #    stale consumer label + promotion counter.
    logging.info("Attempting remove-relation --force on the dead relation (ticket Issue 1)")
    model_2.cli(
        "remove-relation", f"{DB_APP_1}:replication-offer", f"{DB_APP_2}:replication", "--force"
    )
    logging.info("Clearing the dead offer with remove-saas --force")
    try:
        model_2.cli("remove-saas", DB_APP_1, "--force")
    except jubilant.CLIError as error:
        haystack = f"{error} {getattr(error, 'stderr', '')} {getattr(error, 'stdout', '')}".lower()
        if "not found" not in haystack:
            raise
        logging.info("Offer already gone; remove-relation --force had cleared it")
    retry_if_cli_error(
        lambda: model_2.wait(
            ready=wait_for_apps_status(jubilant.all_active, DB_APP_2), timeout=20 * MINUTE_SECS
        )
    )

    # 4. Deploy the fresh cluster db3 in the third model (deferred until after the
    #    primary died to keep the concurrent pod count down on single-node
    #    Kubernetes), then re-establish async replication from the promoted db2.
    logging.info("Deploying the fresh cluster db3")
    configuration = {"profile": "testing"}
    constraints = {"arch": architecture.architecture}
    resources = {"postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"]}
    model_3 = Juju(model=third_model)
    model_3.deploy(
        charm=charm,
        app=DB_APP_3,
        base="ubuntu@24.04",
        config=configuration,
        constraints=constraints,
        resources=resources,
        num_units=3,
        trust=True,
    )
    model_3.deploy(
        charm=DB_TEST_APP_NAME,
        app=DB_TEST_APP_3,
        base="ubuntu@24.04",
        channel="latest/edge",
        constraints=constraints,
        num_units=1,
    )
    model_3.integrate(f"{DB_TEST_APP_3}:database", f"{DB_APP_3}:database")
    retry_if_cli_error(
        lambda: model_3.wait(
            ready=wait_for_apps_status(jubilant.all_active, DB_APP_3, DB_TEST_APP_3),
            timeout=25 * MINUTE_SECS,
        )
    )

    logging.info("Offering db2 and relating the fresh cluster db3")
    retry_if_cli_error(
        lambda: model_2.offer(f"{second_model}.{DB_APP_2}", endpoint="replication-offer")
    )
    retry_if_cli_error(lambda: model_3.consume(f"{second_model}.{DB_APP_2}"))
    retry_if_cli_error(lambda: model_3.integrate(DB_APP_2, f"{DB_APP_3}:replication"))
    retry_if_cli_error(
        lambda: model_2.wait(
            ready=wait_for_apps_status(jubilant.any_active, DB_APP_2), timeout=10 * MINUTE_SECS
        )
    )
    retry_if_cli_error(
        lambda: model_3.wait(
            ready=wait_for_apps_status(jubilant.any_active, DB_APP_3), timeout=10 * MINUTE_SECS
        )
    )

    # 5. THE TICKET POINT: create-replication must SUCCEED, not deadlock on a label
    #    collision. The action itself clears an orphaned promoted-cluster-counter
    #    before its guard runs (no update-status round-trip needed); on the pre-fix
    #    charm it fails with the label collision every time and this raises.
    logging.info("Running create replication action on the recovered primary")
    retry_if_cli_error(
        lambda: model_2.run(
            unit=get_app_leader(model_2, DB_APP_2),
            action="create-replication",
            wait=5 * MINUTE_SECS,
        ).raise_on_failure()
    )

    retry_if_cli_error(
        lambda: model_2.wait(
            ready=wait_for_apps_status(jubilant.all_active, DB_APP_2), timeout=20 * MINUTE_SECS
        )
    )
    retry_if_cli_error(
        lambda: model_3.wait(
            ready=wait_for_apps_status(jubilant.all_active, DB_APP_3), timeout=20 * MINUTE_SECS
        )
    )

    # The promoted db2 owns the shared secret with NO label — proof the owner-create
    # did not collide with any stale consumer alias (DPE-10203).
    assert get_async_secret_labels(model_2, DB_APP_2) == set(), (
        "db2 owns the async-replication secret under a label"
    )
    # db3 is the standby of the recovered primary.
    assert not consumer_alias_exists(model_3, DB_APP_3, FORBIDDEN_LABEL), (
        "db3 registered a stale-prone consumer-side label alias"
    )
    # The fresh re-replication target carries the pre-death data — the whole point
    # of the recovered async replication leg.
    assert all(
        get_db_max_written_value(model_3, DB_APP_3, unit, DB_NAME_1) == max_written
        for unit in get_app_units(model_3, DB_APP_3)
    ), "pre-death data did not re-replicate to the fresh cluster"
