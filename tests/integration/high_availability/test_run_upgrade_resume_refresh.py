#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Behaviour tests for the resume-refresh retry window inside ``run_upgrade``.

The charm_refresh action ``resume-refresh`` is a one-shot state-machine probe: it fails with
"No refresh in progress" once the pod-level StatefulSet roll has already completed (all pods
recreated, even if the last charm hooks are still settling), and with "Unit N is unhealthy.
Refresh will not resume." while the charm-side pause has not yet opened the allow-gate. Under
runner load both windows are minutes wide, so the helper must retry within a real time budget,
and "No refresh in progress" may only be treated as benign termination when the refresh is
verified to have actually completed (charm revision advanced + workload version evidence).

These tests exercise the helpers against a stub ``jubilant.Juju``. They run inside the
integration dependency group (the helpers import jubilant/tenacity) but do not need a live
Juju model.
"""

import pytest
from jubilant import Task, TaskError
from jubilant.statustypes import Status

from . import high_availability_helpers_new as helpers


def build_status(
    charm_rev: int,
    units: dict[str, tuple[str, str, bool]],
    app_name: str = "postgresql-k8s",
    unit_messages: dict[str, str] | None = None,
) -> Status:
    """Build a jubilant Status: units map to (workload status, workload version, leader)."""
    unit_messages = unit_messages or {}
    return Status._from_dict({
        "model": {
            "name": "test",
            "type": "iaas",
            "cloud": "localhost",
            "region": "localhost",
            "controller": "localhost",
            "version": "3.6",
            "model-status": {"current": "available"},
        },
        "machines": {},
        "controller": {},
        "applications": {
            app_name: {
                "charm": f"local:{app_name}+{charm_rev}",
                "charm-origin": "local",
                "charm-name": app_name,
                "charm-rev": charm_rev,
                "exposed": False,
                "application-status": {"current": "active", "message": ""},
                "units": {
                    name: {
                        "workload-status": {
                            "current": current,
                            "version": version,
                            "message": unit_messages.get(name, ""),
                        },
                        "leader": leader,
                    }
                    for name, (current, version, leader) in units.items()
                },
            }
        },
    })


class StubJuju:
    """Minimal stub for the Juju surface used by the resume-refresh flow."""

    def __init__(self, statuses: list[Status], runs: dict[str, list[Task | TaskError]]):
        self.statuses = statuses
        self.refreshed = False
        self.status_calls = 0
        self.runs: dict[str, Task] = {}
        self.last_results: dict[str, Task | TaskError] = {}
        self._runs = runs

    def status(self) -> Status:
        if self.status_calls < len(self.statuses):
            status = self.statuses[self.status_calls]
        else:
            status = self.statuses[-1]
        self.status_calls += 1
        return status

    def refresh(self, app: str, path: str, resources=None) -> None:
        self.refreshed = True

    def run(self, unit: str, action: str, params: dict | None = None, wait: float | None = None):
        queued = self._runs.get(action, [])
        if queued:
            result = queued.pop(0)
            self.last_results[action] = result
        else:
            # Repeat the last consumed result of this action (errors stay errors until
            # explicitly superseded by a success entry in the queue).
            if action in self.last_results:
                result = self.last_results[action]
            else:
                result = Task(id=action, status="completed", message="")
        if isinstance(result, TaskError):
            raise result
        self.runs[action] = result
        return result

    def wait(self, ready=None, timeout=None) -> None:
        pass

    def secrets(self):
        return []


@pytest.fixture
def short_retry(monkeypatch):
    """Shrink the retry budget/wait so exhaustion paths stay fast."""
    monkeypatch.setattr(helpers, "RESUME_REFRESH_RETRY_BUDGET_SECS", 0.2)
    monkeypatch.setattr(helpers, "RESUME_REFRESH_RETRY_WAIT_SECS", 0.01)


def no_refresh_error() -> TaskError:
    return TaskError(Task(id="1", status="failed", message="No refresh in progress"))


def unhealthy_error(unit: int = 2) -> TaskError:
    return TaskError(
        Task(
            id="1", status="failed", message=f"Unit {unit} is unhealthy. Refresh will not resume."
        )
    )


def unrelated_error() -> TaskError:
    return TaskError(Task(id="1", status="failed", message="PostgreSQL is not running on unit 0"))


FULLY_REFRESHED_UNITS = {
    "postgresql-k8s/0": ("active", "16.9 (Debian 16.9-1)", False),
    "postgresql-k8s/1": ("active", "16.9 (Debian 16.9-1)", True),
    "postgresql-k8s/2": ("active", "16.9 (Debian 16.9-1)", False),
}


def test_resume_refresh_is_benign_when_refresh_verified_complete(short_retry):
    """'No refresh in progress' is benign when the refresh rolled the app completely."""
    previous_rev = 958
    juju = StubJuju(
        statuses=[build_status(charm_rev=1042, units=FULLY_REFRESHED_UNITS)],
        runs={"resume-refresh": [no_refresh_error()]},
    )

    helpers.resume_refresh(juju, "postgresql-k8s", previous_rev)

    # Both the leaderboard lookup and the verification saw the post-refresh status.
    assert juju.status_calls == 2


def test_resume_refresh_fails_when_no_refresh_without_revision_advance(short_retry):
    """'No refresh in progress' must NOT be benign when the charm revision never advanced.

    charm_rev unchanged means the roll never happened; treating the message as benign here
    would bless a refresh that never ran.
    """
    juju = StubJuju(
        statuses=[
            build_status(
                charm_rev=958,
                units=FULLY_REFRESHED_UNITS,
            )
        ],
        runs={"resume-refresh": [no_refresh_error()] * 100},
    )

    with pytest.raises(TaskError, match="No refresh in progress"):
        helpers.resume_refresh(juju, "postgresql-k8s", 958)


def test_resume_refresh_fails_when_no_refresh_mid_roll(short_retry):
    """'No refresh in progress' must NOT be benign while units are not all active.

    This pins the half-rolled StatefulSet hazard: pods may all be on the new controller
    revision (so charm-refresh says 'no refresh in progress') while charm-level completion is
    still pending for a unit; only verified completion may break the loop.
    """
    mid_roll = {
        "postgresql-k8s/0": ("maintenance", "16.9 (Debian 16.9-1)", False),
        "postgresql-k8s/1": ("active", "16.9 (Debian 16.9-1)", True),
        "postgresql-k8s/2": ("active", "16.9 (Debian 16.9-1)", False),
    }
    juju = StubJuju(
        statuses=[build_status(charm_rev=1042, units=mid_roll)],
        runs={"resume-refresh": [no_refresh_error()] * 100},
    )

    with pytest.raises(TaskError, match="No refresh in progress"):
        helpers.resume_refresh(juju, "postgresql-k8s", 958)


def test_resume_refresh_retries_through_unhealthy_and_succeeds(short_retry):
    """'Unit N is unhealthy. Refresh will not resume.' must stay retryable, not fatal.

    The allow-gate opens late under load; a later retry must win the resume instead of the
    helper burning its whole window within seconds.
    """
    juju = StubJuju(
        statuses=[build_status(charm_rev=1042, units=FULLY_REFRESHED_UNITS)],
        runs={
            "resume-refresh": [
                unhealthy_error(2),
                unhealthy_error(2),
                unhealthy_error(2),
                Task(id="resume-refresh", status="completed", message=""),
            ]
        },
    )

    helpers.resume_refresh(juju, "postgresql-k8s", 958)


def test_resume_refresh_reraises_unrelated_failures(short_retry):
    """Failures that look nothing like the benign/race flavors keep failing the run."""
    juju = StubJuju(
        statuses=[build_status(charm_rev=1042, units=FULLY_REFRESHED_UNITS)],
        runs={"resume-refresh": [unrelated_error()] * 100},
    )

    with pytest.raises(TaskError, match="PostgreSQL is not running on unit 0"):
        helpers.resume_refresh(juju, "postgresql-k8s", 958)


def test_run_upgrade_passes_the_pre_refresh_revision(monkeypatch):
    """``run_upgrade`` verifies benign 'No refresh in progress' against the pre-refresh revision.

    The revision is captured before juju.refresh; if the helper instead read the post-refresh
    status for the comparison, the benign path would silently never engage.
    """
    monkeypatch.setattr(helpers, "RESUME_REFRESH_RETRY_BUDGET_SECS", 1)
    monkeypatch.setattr(helpers, "RESUME_REFRESH_RETRY_WAIT_SECS", 0.01)

    before = build_status(charm_rev=958, units=FULLY_REFRESHED_UNITS)
    blocked = build_status(
        charm_rev=1042,
        units=FULLY_REFRESHED_UNITS,
        unit_messages={"postgresql-k8s/2": "Refresh incompatible"},
    )
    after = build_status(charm_rev=1042, units=FULLY_REFRESHED_UNITS)
    juju = StubJuju(
        statuses=[before, blocked, blocked, after],
        runs={
            "force-refresh-start": [],
            "resume-refresh": [no_refresh_error()],
        },
    )

    helpers.run_upgrade(juju, "postgresql-k8s", "postgresql-k8s.charm")

    assert "force-refresh-start" in juju.runs
