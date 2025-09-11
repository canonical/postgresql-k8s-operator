# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import subprocess

import jubilant
import psycopg2
import pytest
import yaml
from tenacity import Retrying, stop_after_delay, wait_fixed

from ..helpers import METADATA

DATABASE_APP_NAME = "postgresql"
SECOND_DATABASE_APP_NAME = "postgresql2"
THIRD_DATABASE_APP_NAME = "postgresql3"

DATA_INTEGRATOR_APP_NAME = "data-integrator"
SECOND_DATA_INTEGRATOR_APP_NAME = "data-integrator2"
THIRD_DATA_INTEGRATOR_APP_NAME = "data-integrator3"
DATA_INTEGRATOR_RELATION = "postgresql"

DATABASE_APP_CONFIG = {"profile": "testing"}

TESTING_DATABASE = "testdb"
TIMEOUT = 2500


def _all_active(status: jubilant.Status, apps: list[str]) -> bool:
    return all(jubilant.all_active(status, app) for app in apps)


def _model_name() -> str:
    status_raw = subprocess.run(["juju", "status", "--format", "json"], capture_output=True).stdout
    data = json.loads(status_raw or b"{}")
    return data.get("model", {}).get("name")


def _build_connection_string(application_name: str, relation_name: str, database: str) -> str:
    # Fetch relation data via juju show-unit
    unit_name = f"{application_name}/0"
    show_unit_raw = subprocess.run(["juju", "show-unit", unit_name], capture_output=True).stdout
    if not show_unit_raw:
        raise RuntimeError(f"Unable to retrieve unit info for {unit_name}")
    data = yaml.safe_load(show_unit_raw)

    relation_infos = [
        r for r in data[unit_name]["relation-info"] if r["endpoint"] == relation_name
    ]
    if not relation_infos:
        raise RuntimeError("No relation data found to build connection string")

    app_data = relation_infos[0]["application-data"]
    # Handle both secret-user and plain username/password
    if secret_uri := app_data.get("secret-user"):
        secret_id = secret_uri.split("/")[-1]
        show_secret_raw = subprocess.run(
            ["juju", "show-secret", "--format", "json", "--reveal", secret_id], capture_output=True
        ).stdout
        secret = json.loads(show_secret_raw)
        secret_data = secret[secret_id]["content"]["Data"]
        username = secret_data["username"]
        password = secret_data["password"]
    else:
        username = app_data["username"]
        password = app_data["password"]

    endpoints = app_data.get("endpoints") or app_data.get("read-only-endpoints")
    host = endpoints.split(",")[0].split(":")[0]

    # Translate service hostname to ClusterIP via kubectl
    name = host.split(".")[0]
    namespace = _model_name()
    svc_json = subprocess.run(
        ["kubectl", "-n", namespace, "get", "svc", name, "-o", "json"], capture_output=True
    ).stdout
    svc = json.loads(svc_json)
    ip = svc["spec"]["clusterIP"]

    return f"dbname='{database}' user='{username}' host='{ip}' password='{password}' connect_timeout=10"


@pytest.mark.abort_on_fail
def test_cycle_detection_three_clusters(juju: jubilant.Juju, charm):
    # Deploy three PostgreSQL clusters and three data-integrators (to create tables)
    resources = {
        "postgresql-image": METADATA["resources"]["postgresql-image"]["upstream-source"],
    }

    if DATABASE_APP_NAME not in juju.status().apps:
        juju.deploy(
            charm,
            app=DATABASE_APP_NAME,
            num_units=1,
            resources=resources,
            trust=True,
            config={"profile": "testing"},
        )
    if SECOND_DATABASE_APP_NAME not in juju.status().apps:
        juju.deploy(
            charm,
            app=SECOND_DATABASE_APP_NAME,
            num_units=1,
            resources=resources,
            trust=True,
            config={"profile": "testing"},
        )
    if THIRD_DATABASE_APP_NAME not in juju.status().apps:
        juju.deploy(
            charm,
            app=THIRD_DATABASE_APP_NAME,
            num_units=1,
            resources=resources,
            trust=True,
            config={"profile": "testing"},
        )

    for app_name in [
        DATA_INTEGRATOR_APP_NAME,
        SECOND_DATA_INTEGRATOR_APP_NAME,
        THIRD_DATA_INTEGRATOR_APP_NAME,
    ]:
        if app_name not in juju.status().apps:
            juju.deploy(
                DATA_INTEGRATOR_APP_NAME,
                app=app_name,
                num_units=1,
                channel="latest/stable",
                config={"database-name": TESTING_DATABASE},
            )

    juju.wait(
        lambda status: _all_active(
            status, [DATABASE_APP_NAME, SECOND_DATABASE_APP_NAME, THIRD_DATABASE_APP_NAME]
        ),
        timeout=TIMEOUT,
    )

    # Integrate data-integrators for table creation
    for provider, requirer in [
        (DATABASE_APP_NAME, DATA_INTEGRATOR_APP_NAME),
        (SECOND_DATABASE_APP_NAME, SECOND_DATA_INTEGRATOR_APP_NAME),
        (THIRD_DATABASE_APP_NAME, THIRD_DATA_INTEGRATOR_APP_NAME),
    ]:
        # avoid duplicate relations
        existing = [
            relation
            for relation in juju.status().apps.get(provider).relations.values()
            if any(True for r in relation if r.related_app == requirer)
        ]
        if not existing:
            juju.integrate(provider, requirer)
    juju.wait(
        lambda status: _all_active(
            status,
            [
                DATABASE_APP_NAME,
                SECOND_DATABASE_APP_NAME,
                THIRD_DATABASE_APP_NAME,
                DATA_INTEGRATOR_APP_NAME,
                SECOND_DATA_INTEGRATOR_APP_NAME,
                THIRD_DATA_INTEGRATOR_APP_NAME,
            ],
        ),
        timeout=600,
    )

    _create_test_table(DATA_INTEGRATOR_APP_NAME, TESTING_DATABASE, "public.test_cycle")
    _create_test_table(SECOND_DATA_INTEGRATOR_APP_NAME, TESTING_DATABASE, "public.test_cycle")
    _create_test_table(THIRD_DATA_INTEGRATOR_APP_NAME, TESTING_DATABASE, "public.test_cycle")

    print("A -> B subscription")
    juju.integrate(
        f"{DATABASE_APP_NAME}:logical-replication-offer",
        f"{SECOND_DATABASE_APP_NAME}:logical-replication",
    )
    juju.wait(lambda status: jubilant.all_active(status, SECOND_DATABASE_APP_NAME), timeout=600)

    pg2_config = DATABASE_APP_CONFIG.copy()
    pg2_config["logical_replication_subscription_request"] = json.dumps({
        TESTING_DATABASE: ["public.test_cycle"],
    })
    juju.config(app=SECOND_DATABASE_APP_NAME, values=pg2_config)

    print("B -> C subscription")
    juju.integrate(
        f"{SECOND_DATABASE_APP_NAME}:logical-replication-offer",
        f"{THIRD_DATABASE_APP_NAME}:logical-replication",
    )
    juju.wait(lambda status: jubilant.all_active(status, THIRD_DATABASE_APP_NAME), timeout=600)

    pg3_config = DATABASE_APP_CONFIG.copy()
    pg3_config["logical_replication_subscription_request"] = json.dumps({
        TESTING_DATABASE: ["public.test_cycle"],
    })
    juju.config(app=THIRD_DATABASE_APP_NAME, values=pg3_config)

    print("Attempt C -> A subscription should be blocked due to cycle detection")
    juju.integrate(
        f"{THIRD_DATABASE_APP_NAME}:logical-replication-offer",
        f"{DATABASE_APP_NAME}:logical-replication",
    )

    pg1_config = DATABASE_APP_CONFIG.copy()
    pg1_config["logical_replication_subscription_request"] = json.dumps({
        TESTING_DATABASE: ["public.test_cycle"],
    })
    juju.config(app=DATABASE_APP_NAME, values=pg1_config)

    # Expect unit of A to go into blocked state (single unit deployment)
    def unit_blocked(status: jubilant.Status) -> bool:
        unit = status.get_units(DATABASE_APP_NAME).get(f"{DATABASE_APP_NAME}/0")
        return unit.workload_status.current == "blocked"

    juju.wait(unit_blocked, timeout=900)


def _create_test_table(data_integrator_app_name: str, database: str, qualified_table: str) -> None:
    connection_string = _build_connection_string(
        data_integrator_app_name,
        DATA_INTEGRATOR_RELATION,
        database=database,
    )
    connection = None
    try:
        for attempt in Retrying(stop=stop_after_delay(120), wait=wait_fixed(3), reraise=True):
            with attempt:
                connection = psycopg2.connect(connection_string)
        connection.autocommit = True
        with connection.cursor() as cursor:
            schema, table = qualified_table.split(".")
            cursor.execute(f"CREATE TABLE IF NOT EXISTS {table} (test_column text);")
    finally:
        if connection is not None:
            connection.close()
