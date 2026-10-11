# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed Kubernetes Operator for PostgreSQL."""

import logging

import ops
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v1.loki_push_api import LogProxyConsumer
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.rolling_ops.v0.rollingops import RollingOpsManager
from ops_tracing import Tracing
from single_kernel_postgresql.charms.k8s_charm import PostgreSQLK8sCharm
from single_kernel_postgresql.config.literals import (
    K8S_POSTGRES_LOG_FILES,
    PEER_RELATION,
    TRACING_RELATION_NAME,
)
from single_kernel_postgresql.events.tls_transfer import TLSTransfer

logger = logging.getLogger(__name__)


class PostgresqlOperatorCharm(PostgreSQLK8sCharm):
    """Charmed Operator for the PostgreSQL database."""

    def __init__(self, *args):
        super().__init__(
            *args,
            restart_lock_acquire=lambda: self.on[
                str(self.restart_manager.name)
            ].acquire_lock.emit(),
        )

        # Show logger name (module name) in logs.
        root_logger = logging.getLogger()
        for handler in root_logger.handlers:
            if isinstance(handler, ops.log.JujuLogHandler):
                handler.setFormatter(logging.Formatter("{name}:{message}", style="{"))

        # The rolling-ops manager is a charm lib; it stays charm-side and its
        # callback lands in the lib events handler (the K8s branch).
        self.restart_manager = RollingOpsManager(
            charm=self,
            relation="restart",
            callback=self.postgresql_events_handler._restart,
        )

        # COS requirers (charm libs stay charm-side) — injected into the lib charm.
        self.grafana_dashboards = GrafanaDashboardProvider(self)
        self.metrics_endpoint = MetricsEndpointProvider(
            self,
            refresh_event=[self.on.start],
            jobs=self._generate_metrics_jobs(self.is_tls_enabled),
        )
        self.loki_push = LogProxyConsumer(
            self,
            logs_scheme={"postgresql": {"log-files": K8S_POSTGRES_LOG_FILES}},
            relation_name="logging",
        )
        self.tracing = Tracing(self, tracing_relation_name=TRACING_RELATION_NAME)

        self.tls_transfer = TLSTransfer(self, PEER_RELATION)


if __name__ == "__main__":
    from ops.main import main

    main(PostgresqlOperatorCharm)
