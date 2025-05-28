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

[Canonical K8s]: /t/15937
[MicroK8s]: /t/11858
[GKE]: /t/11237
[EKS]: /t/12106
[AKS]: /t/14307
[Multi-AZ]: /t/15678
[Terraform]: /t/14924
[Air-gapped]: /t/15691

[Integrate with another application]: /t/9594
[External access]: /t/15701
[Scale replicas]: /t/9592
[Enable TLS]: /t/9593
[Enable LDAP]: /t/17189
[Enable plugins/extensions]: /t/10907

[Configure S3 AWS]: /t/9595
[Configure S3 RadosGW]: /t/10316
[Create a backup]: /t/9596
[Restore a backup]: /t/9597
[Manage backup retention]: /t/14203
[Migrate a cluster]: /t/9598

[Enable monitoring]: /t/10812
[Enable alert rules]: /t/12982
[Enable tracing]: /t/14786

[Perform a minor upgrade]: /t/12095
[Perform a minor rollback]: /t/12096

[Cross-regional async replication]: /t/15413
[Set up clusters]: /t/13895
[Integrate with a client app]: /t/13896
[Remove or recover a cluster]: /t/13897

[Integrate with your charm]: /t/11853
[Migrate data via pg_dump]: /t/12162
[Migrate data via backup/restore]: /t/12161