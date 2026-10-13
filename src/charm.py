# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed Kubernetes Operator for PostgreSQL."""

import logging

# First platform-specific import; on wrong architectures fall back to a
# blocked warning charm (the wheel for this arch is unavailable).
try:
    from single_kernel_postgresql.charms.k8s_charm import PostgreSQLK8sCharm
except ModuleNotFoundError:
    from ops.main import main
    from single_kernel_postgresql.utils.arch import (
        WrongArchitectureWarningCharm,
        is_wrong_architecture,
    )

    if is_wrong_architecture() and __name__ == "__main__":
        main(WrongArchitectureWarningCharm, use_juju_for_storage=True)
    raise

import ops.log
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v1.loki_push_api import LogProxyConsumer
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.rolling_ops.v0.rollingops import RollingOpsManager
from ops_tracing import Tracing
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

        # The rolling-ops manager is a charm lib; it stays charm-side. It resolves
        # its callback BY NAME on the charm (getattr(charm, callback.__name__)), so
        # the charm must carry a named method delegating to the lib events handler.
        self.restart_manager = RollingOpsManager(
            charm=self,
            relation="restart",
            callback=self._restart,
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

    def _restart(self, event) -> None:
        """Delegate to the lib events handler (rolling-ops resolves the callback by name)."""
        self.postgresql_events_handler._restart(event)


if __name__ == "__main__":
    from ops.main import main

    main(PostgresqlOperatorCharm, use_juju_for_storage=True)
