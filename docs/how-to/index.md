# How-to guides

The following guides cover key processes and common tasks for managing and using Charmed PostgreSQL on Kubernetes.

## Deployment and setup

Installation of different cloud services with Juju:
* [Canonical K8s]
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
* [How to upgrade]

## Cross-regional (cluster-cluster) async replication

* [Cross-regional async replication]
    * [Set up clusters]
    * [Integrate with a client app]
    * [Remove or recover a cluster]

## Development

This section is for charm developers looking to support PostgreSQL integrations with their charm.

* [Integrate with your charm]
* [Migrate data via pg_dump]
* [Migrate data via backup/restore]

<!--Links-->

[Canonical K8s]: /how-to/deploy/canonical-k8s
[GKE]: /how-to/deploy/gke
[EKS]: /how-to/deploy/eks
[AKS]: /how-to/deploy/aks
[Multi-AZ]: /how-to/deploy/multi-az
[Terraform]: /how-to/deploy/terraform
[Air-gapped]: /how-to/deploy/air-gapped

[Integrate with another application]: /how-to/integrate-with-another-application
[External access]: /how-to/external-network-access
[Scale replicas]: /how-to/scale-replicas
[Enable TLS]: /how-to/enable-tls
[Enable LDAP]: /how-to/enable-ldap
[Enable plugins/extensions]: /how-to/enable-plugins-extensions

[Configure S3 AWS]: /how-to/back-up-and-restore/configure-s3-aws
[Configure S3 RadosGW]: /how-to/back-up-and-restore/configure-s3-radosgw
[Create a backup]: /how-to/back-up-and-restore/create-a-backup
[Restore a backup]: /how-to/back-up-and-restore/restore-a-backup
[Manage backup retention]: /how-to/back-up-and-restore/manage-backup-retention
[Migrate a cluster]: /how-to/back-up-and-restore/migrate-a-cluster

[Enable monitoring]: /how-to/monitoring-cos/enable-monitoring
[Enable alert rules]: /how-to/monitoring-cos/enable-alert-rules
[Enable tracing]: /how-to/monitoring-cos/enable-tracing

[How to upgrade]: /how-to/upgrade/index

[Cross-regional async replication]: /how-to/cross-regional-async-replication/index
[Set up clusters]: /how-to/cross-regional-async-replication/set-up-clusters
[Integrate with a client app]: /how-to/cross-regional-async-replication/integrate-with-a-client-app
[Remove or recover a cluster]: /how-to/cross-regional-async-replication/remove-or-recover-a-cluster

[Integrate with your charm]: /how-to/development/integrate-with-your-charm
[Migrate data via pg_dump]: /how-to/development/migrate-data-via-pg-dump
[Migrate data via backup/restore]: /how-to/development/migrate-data-via-backup-restore


```{toctree}
:titlesonly:
:maxdepth: 2
:glob:
:hidden:

Deploy <deploy/index>
Integrate <integrate-with-another-application>
Manage passwords <manage-passwords>
External network access <external-network-access>
Scale <scale-replicas>
Enable TLS <enable-tls>
Enable LDAP <enable-ldap>
Enable plugins/extensions <enable-plugins-extensions>
Back up and restore <back-up-and-restore/index>
Monitoring (COS) <monitoring-cos/index>
Upgrade <upgrade/index>
Cross-regional async replication <cross-regional-async-replication/index>
Development <development/index>
