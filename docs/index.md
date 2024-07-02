# Charmed PostgreSQL K8s Documentation

Charmed PostgreSQL K8s is an open-source operator designed to deploy and operate object-relational databases on IAAS/VM. It packages the powerful database management system [PostgreSQL](https://www.postgresql.org/) into a charmed operator for deployment with [Juju](https://juju.is/docs/juju).

This charm offers automated operations management from day 0 to day 2. It is equipped with several features to securely store and scale complicated data workloads, including TLS encryption, backups, monitoring, password rotation, and easy integration with client applications.

Charmed PostgreSQL K8s meets the need of deploying PostgreSQL in a structured and consistent manner while providing flexibility in configuration. It simplifies deployment, scaling, configuration and management of relational databases in large-scale production environments reliably.
 
This charmed operator is made for anyone looking for a comprehensive database management interface, whether for operating a complex production environment or simply as a playground to learn more about databases and charms.

[note type="positive"]
This operator is built for **Kubernetes**.

For deployments in **IAAS/VM**, see  [Charmed PostgreSQL](https://charmhub.io/postgresql).
[/note]

<!--
This modern "Charmed PostgreSQL K8s" operator (in channel `14/stable`) is a new "[Charmed SDK](https://juju.is/docs/sdk)"-based charm that replaces the legacy "[Reactive](https://juju.is/docs/sdk/charm-taxonomy#heading--reactive)"-based charm (in channel `latest/stable`).<br/>Read more about [legacy charms here](/t/11013).
-->

| | |
|--|--|
|  [**Tutorials**](/t/9296)</br>  [Get started](/t/9296) - a hands-on introduction to using Charmed PostgreSQL K8s operator for new users </br> |  [**How-to guides**](/t/9592) </br> Step-by-step guides covering key operations such as [scaling](/t/9592), [encryption](/t/9593), and [restoring backups](/t/9597) |
| [**Reference**](/t/13976) </br> Technical information such as [requirements](/t/11744), [release notes](/t/11872), and [plugins](/t/10945) | [**Explanation**](/t/11856) </br> Concepts - discussion and clarification of key topics such as [architecture](/t/11856), [users](/t/10843), and [legacy charms](/t/11013)|

## Project and community
Charmed PostgreSQL K8s is an official distribution of PostgreSQL. It’s an open-source project that welcomes community contributions, suggestions, fixes and constructive feedback.
- [Read our Code of Conduct](https://ubuntu.com/community/code-of-conduct)
- [Join the Discourse forum](https://discourse.charmhub.io/tag/postgresql)
- [Contribute](https://github.com/canonical/postgresql-k8s-operator/blob/main/CONTRIBUTING.md) and report [issues](https://github.com/canonical/postgresql-operator/issues/new/choose)
- Explore [Canonical Data Fabric solutions](https://canonical.com/data)
- [Contact us](/t/11852) for all further questions

## Licencing & Trademark
The Charmed PostgreSQL Operator is distributed under the [Apache Software Licence version 2.0](https://github.com/canonical/postgresql-operator/blob/main/LICENSE). It depends on [PostgreSQL](https://www.postgresql.org/ftp/source/), which is licensed under the [PostgreSQL License](https://www.postgresql.org/about/licence/) - a liberal open-source licence similar to the BSD or MIT licences.

PostgreSQL is a trademark or registered trademark of PostgreSQL Global Development Group. Other trademarks are the property of their respective owners.

# Contents

1. [Tutorial](tutorial)
  1. [Overview](tutorial/t-overview.md)
  1. [1. Set up the environment](tutorial/t-set-up.md)
  1. [2. Deploy PostgreSQL](tutorial/t-deploy.md)
  1. [3. Access PostgreSQL](tutorial/t-access.md)
  1. [4. Scale replicas](tutorial/t-scale.md)
  1. [5. Manage passwords](tutorial/t-passwords.md)
  1. [6. Integrate with other applications](tutorial/t-integrate.md)
  1. [7. Enable TLS](tutorial/t-enable-tls.md)
  1. [8. Clean up environment](tutorial/t-clean-up.md)
1. [How-to guides](how-to)
  1. [Set up](how-to/h-set-up)
    1. [Deploy on MicroK8s](how-to/h-set-up/h-deploy-microk8s.md)
    1. [Deploy on GKE](how-to/h-set-up/h-deploy-gke.md)
    1. [Deploy on EKS](how-to/h-set-up/h-deploy-eks.md)
    1. [Deploy on AKS](how-to/h-set-up/h-deploy-aks.md)
    1. [Scale units](how-to/h-set-up/h-scale.md)
    1. [Enable TLS](how-to/h-set-up/h-enable-tls.md)
    1. [Manage client applications](how-to/h-set-up/h-manage-client.md)
  1. [Back up and restore](how-to/h-backups)
    1. [Configure S3 AWS](how-to/h-backups/h-configure-s3-aws.md)
    1. [Configure S3 RadosGW](how-to/h-backups/h-configure-s3-radosgw.md)
    1. [Create a backup](how-to/h-backups/h-create-backup.md)
    1. [Restore a backup](how-to/h-backups/h-restore-backup.md)
    1. [Manage backup retention](how-to/h-backups/h-manage-backup-retention.md)
    1. [Migrate a cluster](how-to/h-backups/h-migrate-cluster.md)
  1. [Monitoring (COS)](how-to/h-monitor)
    1. [Enable monitoring](how-to/h-monitor/h-enable-monitoring.md)
    1. [Enable Alert Rules](how-to/h-monitor/h-enable-alert-rules.md)
  1. [Upgrade](how-to/h-upgrade)
    1. [Overview](how-to/h-upgrade/h-upgrade-intro.md)
    1. [Perform a major upgrade](how-to/h-upgrade/h-upgrade-major.md)
    1. [Perform a major rollback](how-to/h-upgrade/h-rollback-major.md)
    1. [Perform a minor upgrade](how-to/h-upgrade/h-upgrade-minor.md)
    1. [Perform a minor rollback](how-to/h-upgrade/h-rollback-minor.md)
  1. [Connect your charm](how-to/h-connect-your-charm)
    1. [Integrate a database with your charm](how-to/h-connect-your-charm/h-integrate-with-your-charm.md)
    1. [Migrate data via...](how-to/h-connect-your-charm/h-connect-migrate)
      1. [...pg_dump](how-to/h-connect-your-charm/h-connect-migrate/h-connect-migrate-pgdump.md)
      1. [...backup/restore](how-to/h-connect-your-charm/h-connect-migrate/h-connect-migrate-backup-restore.md)
  1. [Cross-regional async replication](how-to/h-async)
    1. [Set up clusters](how-to/h-async/h-async-set-up.md)
    1. [Integrate with a client app](how-to/h-async/h-async-integrate.md)
    1. [Remove or recover a cluster](how-to/h-async/h-async-remove-recover.md)
  1. [Enable plugins/extensions](how-to/h-enable-plugins-extensions.md)
1. [Reference](reference)
  1. [Overview](reference/r-overview.md)
  1. [Release Notes](reference/r-releases-group)
    1. [All releases](reference/r-releases-group/r-releases.md)
    1. [Revision 280/281](reference/r-releases-group/r-releases-rev280.md)
    1. [Revision 193](reference/r-releases-group/r-releases-rev193.md)
    1. [Revision 177](reference/r-releases-group/r-releases-rev177.md)
    1. [Revision 158](reference/r-releases-group/r-releases-rev158.md)
    1. [Revision 73](reference/r-releases-group/r-releases-rev73.md)
  1. [System requirements](reference/r-system-requirements.md)
  1. [Software testing](reference/r-software-testing.md)
  1. [Performance and resource allocation](reference/r-performance.md)
  1. [Troubleshooting](reference/h-troubleshooting.md)
  1. [Plugins/extensions](reference/r-plugins-extensions.md)
  1. [Contacts](reference/r-contacts.md)
1. [Explanation](explanation)
  1. [Architecture](explanation/e-architecture.md)
  1. [Interfaces/endpoints](explanation/e-interfaces-endpoints.md)
  1. [Statuses](explanation/e-statuses.md)
  1. [Users](explanation/e-users.md)
  1. [Logs](explanation/e-logs.md)
  1. [Juju](explanation/e-juju-details.md)
  1. [Legacy charm](explanation/e-legacy-charm.md)
  1. [Flowcharts](explanation/flowcharts)
    1. [Charm](explanation/flowcharts/e-flowchart-charm.md)
    1. [Relations](explanation/flowcharts/e-flowchart-peers.md)
    1. [Backups](explanation/flowcharts/e-flowchart-backups.md)
1. [Search](https://canonical.com/data/docs/postgresql/k8s)