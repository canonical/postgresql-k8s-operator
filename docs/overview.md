> This is a **Kubernetes** operator. To deploy on IAAS/VM, see [Charmed PostgreSQL VM](https://charmhub.io/postgresql).

# Charmed PostgreSQL K8s documentation

Charmed PostgreSQL K8s is an open-source operator designed to deploy and operate object-relational databases on IAAS/VM. It packages the powerful database management system [PostgreSQL](https://www.postgresql.org/) into a charmed operator for deployment with [Juju](https://juju.is/docs/juju).

This charm offers automated operations management from day 0 to day 2. It is equipped with several features to securely store and scale complicated data workloads, including TLS encryption, backups, monitoring, password rotation, and easy integration with client applications.

Charmed PostgreSQL K8s meets the need of deploying PostgreSQL in a structured and consistent manner while providing flexibility in configuration. It simplifies deployment, scaling, configuration and management of relational databases in large-scale production environments reliably.
 
This charmed operator is made for anyone looking for a comprehensive database management interface, whether for operating a complex production environment or simply as a playground to learn more about databases and charms.


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

# Navigation

[details=Navigation]

| Level | Path | Navlink |
|--------|--------|-------------|
| 1 | tutorial | [Tutorial](/t/9296) |
| 2 | t-set-up | [1. Set up environment](/t/9297) |
| 2 | t-deploy | [2. Deploy PostgreSQL](/t/9298) |
| 2 | t-access | [3. Access PostgreSQL](/t/13702) |
| 2 | t-scale | [4. Scale replicas](/t/9299) |
| 2 | t-manage-passwords | [5. Manage passwords](/t/9300) |
| 2 | t-integrate | [6. Integrate with other applications](/t/9301) |
| 2 | t-enable-tls | [7. Enable TLS encryption](/t/9302) |
| 2 | t-clean-up | [8. Clean up environment](/t/9303) |
| 1 | how-to | [How-to guides](/t/16767) |
| 2 | h-deploy | [Deploy](/t/16810) |
| 3 | h-deploy-canonical-k8s | [Canonical K8s](/t/15937) |
| 3 | h-deploy-gke | [GKE](/t/11237) |
| 3 | h-deploy-eks | [EKS](/t/12106) |
| 3 | h-deploy-aks | [AKS](/t/14307) |
| 3 | h-deploy-multi-az | [Multi-AZ](/t/15678) |
| 3 | h-deploy-terraform | [Terraform](/t/14924) |
| 3 | h-deploy-airgapped | [Air-gapped](/t/15691) |
| 2 | h-integrate | [Integrate with another application](/t/9594) |
| 2 | h-manage-passwords | [Manage passwords](/t/17860) |
| 2 | h-external-access | [External network access](/t/15701) |
| 2 | h-scale | [Scale replicas](/t/9592) |
| 2 | h-enable-tls | [Enable TLS](/t/9593) |
| 2 | h-enable-ldap | [Enable LDAP](/t/17189) |
| 2 | h-enable-plugins-extensions | [Enable plugins/extensions](/t/10907) |
| 2 | h-backup | [Back up and restore]() |
| 3 | h-configure-s3-aws | [Configure S3 AWS](/t/9595) |
| 3 | h-configure-s3-radosgw | [Configure S3 RadosGW](/t/10316) |
| 3 | h-create-backup | [Create a backup](/t/9596) |
| 3 | h-restore-backup | [Restore a backup](/t/9597) |
| 3 | h-manage-backup-retention | [Manage backup retention](/t/14203) |
| 3 | h-migrate-cluster | [Migrate a cluster](/t/9598) |
| 2 | h-monitor | [Monitoring (COS)]() |
| 3 | h-enable-monitoring | [Enable monitoring](/t/10812) |
| 3 | h-enable-alert-rules | [Enable alert rules](/t/12982) |
| 3 | h-enable-tracing | [Enable tracing](/t/14786) |
| 2 | h-upgrade | [Upgrade](/t/12092) |
| 3 | h-upgrade-minor | [Perform a minor upgrade](/t/12095) |
| 3 | h-rollback-minor | [Perform a minor rollback](/t/12096) |
| 2 | h-async | [Cross-regional async replication](/t/15413) |
| 3 | h-async-set-up | [Set up clusters](/t/13895) |
| 3 | h-async-integrate | [Integrate with a client app](/t/13896) |
| 3 | h-async-remove-recover | [Remove or recover a cluster](/t/13897) |
| 2 | h-development| [Development]() |
| 3 | h-development-integrate | [Integrate with your charm](/t/11853) |
| 3 | h-migrate-pgdump | [Migrate data via pg_dump](/t/12162) |
| 3 | h-migrate-backup-restore | [Migrate data via backup/restore](/t/12161) |
| 1 | reference | [Reference](/t/13977) |
| 2 | r-releases | [Releases](/t/11872) |
| 2 | r-system-requirements | [System requirements](/t/11744) |
| 2 | r-software-testing | [Software testing](/t/11774) |
| 2 | r-performance | [Performance and resources](/t/11975) |
| 2 | r-troubleshooting | [Troubleshooting](/t/11854) |
| 2 | r-plugins-extensions | [Plugins/extensions](/t/10945) |
| 2 | r-alert-rules | [Alert rules](/t/15840) |
| 2 | e-statuses | [Statuses](/t/11855) |
| 2 | r-contacts | [Contacts](/t/11852) |
| 1 | explanation | [Explanation](/t/16769) |
| 2 | e-architecture | [Architecture](/t/11856) |
| 2 | e-interfaces-endpoints | [Interfaces/endpoints](/t/10252) |
| 2 | e-juju-details | [Juju](/t/11986) |
| 2 | e-legacy-charm | [Legacy charm](/t/11013) |
| 2 | e-users | [Users](/t/10843) |
| 2 | e-logs | [Logs](/t/12098) |
| 2 | e-connection-pooling| [Connection pooling](/t/15799) |
| 2 | e-security | [Security](/t/16850) |
| 3 | e-cryptography | [Cryptography](/t/16851) |
| 2 | flowcharts | [Flowcharts]() |
| 3 | e-flowchart-charm | [Charm](/t/9305) |
| 3 | e-flowchart-peers | [Relations](/t/9306) |
| 3 | e-flowchart-backups | [Backups](/t/10248) |
| 1 | search | [Search](https://canonical.com/data/docs/postgresql/k8s) |

[/details]

<!-- Archived
| 3 | h-upgrade-intro | [Overview](/t/12092) |
| 3 | h-upgrade-major | [Perform a major upgrade](/t/12093) |
| 3 | h-rollback-major | [Perform a major rollback](/t/12094) |

| 3 | h-deploy-microk8s | [MicroK8s](/t/11858) |

| 3 | r-revision-462-463 | [Revision 462/463](/t/16008) |
| 3 | r-revision-444-445 | [Revision 444/445](/t/15966) |
| 3 | r-revision-381-382 | [Revision 381/382](/t/15442) |
| 3 | r-revision-280-281 | [Revision 280/281](/t/14068) |
| 3 | r-revision-193 | [Revision 193](/t/13208) |
| 3 | r-revision-177 | [Revision 177](/t/12668) |
| 3 | r-revision-158 | [Revision 158](/t/11874) |
| 3 | r-revision-73 | [Revision 73](/t/11873) |
-->