


# How-to guides

The following guides cover key processes and common tasks for managing and using Charmed PostgreSQL on Kubernetes.

## Deployment and setup

Installation of different cloud services with Juju:
* [Canonical K8s]
* [MicroK8s]
* [GKE]
* [EKS]
* [AKS]
* [Multi-availability zones (AZ)][Multi-AZ]

Other deployment scenarios and configurations:
* [Terraform]
* [Air-gapped]

## Usage and maintenance

* [Integrate with another application]
* [External access]
* [Scale replicas]
* [Enable TLS]
* [Enable LDAP]
* [Enable plugins/extensions]

## Backup and restore
* [Configure S3 AWS]
* [Configure S3 RadosGW]
* [Create a backup]
* [Restore a backup]
* [Manage backup retention]
* [Migrate a cluster]

## Monitoring (COS)

* [Enable monitoring] with Grafana
* [Enable alert rules] with Prometheus
* [Enable tracing] with Parca

## Minor upgrades
* [Perform a minor upgrade]
* [Perform a minor rollback]

## Cross-regional (cluster-cluster) async replication

* [Cross-regional async replication]
    * [Set up clusters]
    * [Integrate with a client app]
    * [Remove or recover a cluster]
    * [Enable plugins/extensions]

## Development

This section is for charm developers looking to support PostgreSQL integrations with their charm.

* [Integrate with your charm]
* [Migrate data via pg_dump]
* [Migrate data via backup/restore]

<!--Links-->

[Canonical K8s]: /how-to-guides/deploy/canonical-k8s
[MicroK8s]: /
[GKE]: /how-to-guides/deploy/gke
[EKS]: /how-to-guides/deploy/eks
[AKS]: /how-to-guides/deploy/aks
[Multi-AZ]: /how-to-guides/deploy/multi-az
[Terraform]: /how-to-guides/deploy/terraform
[Air-gapped]: /how-to-guides/deploy/air-gapped

[Integrate with another application]: /how-to-guides/integrate-with-another-application
[External access]: /how-to-guides/external-network-access
[Scale replicas]: /how-to-guides/scale-replicas
[Enable TLS]: /how-to-guides/enable-tls
[Enable LDAP]: /how-to-guides/enable-ldap
[Enable plugins/extensions]: /how-to-guides/enable-plugins-extensions

[Configure S3 AWS]: /how-to-guides/back-up-and-restore/configure-s3-aws
[Configure S3 RadosGW]: /how-to-guides/back-up-and-restore/configure-s3-radosgw
[Create a backup]: /how-to-guides/back-up-and-restore/create-a-backup
[Restore a backup]: /how-to-guides/back-up-and-restore/restore-a-backup
[Manage backup retention]: /how-to-guides/back-up-and-restore/manage-backup-retention
[Migrate a cluster]: /how-to-guides/back-up-and-restore/migrate-a-cluster

[Enable monitoring]: /how-to-guides/monitoring-cos/enable-monitoring
[Enable alert rules]: /how-to-guides/monitoring-cos/enable-alert-rules
[Enable tracing]: /how-to-guides/monitoring-cos/enable-tracing

[Perform a minor upgrade]: /how-to-guides/upgrade/perform-a-minor-upgrade
[Perform a minor rollback]: /how-to-guides/upgrade/perform-a-minor-rollback

[Cross-regional async replication]: /how-to-guides/cross-regional-async-replication/index
[Set up clusters]: /how-to-guides/cross-regional-async-replication/set-up-clusters
[Integrate with a client app]: /how-to-guides/cross-regional-async-replication/integrate-with-a-client-app
[Remove or recover a cluster]: /how-to-guides/cross-regional-async-replication/remove-or-recover-a-cluster

[Integrate with your charm]: /how-to-guides/development/integrate-with-your-charm
[Migrate data via pg_dump]: /how-to-guides/development/migrate-data-via-pg-dump
[Migrate data via backup/restore]: /how-to-guides/development/migrate-data-via-backup-restore


```{toctree}
:titlesonly:
:maxdepth: 2
:glob:
:hidden:

*
*/index
