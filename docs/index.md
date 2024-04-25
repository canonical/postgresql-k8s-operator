The Charmed PostgreSQL K8s Operator delivers automated operations management from day 0 to day 2 on the [PostgreSQL Database Management System](https://www.postgresql.org/). It is an open source, end-to-end, production-ready data platform on top of [Juju](https://juju.is/).

PostgreSQL is a powerful, open source object-relational database system that uses and extends the SQL language combined with many features that safely store and scale the most complicated data workloads.

The Charmed PostgreSQL K8s operator comes in two flavours to deploy and operate PostgreSQL on [physical/virtual machines](https://github.com/canonical/postgresql-operator) and [Kubernetes](https://github.com/canonical/postgresql-k8s-operator). Both offer features such as replication, TLS, password rotation, and easy to use integration with applications. The Charmed PostgreSQL K8s Operator meets the need of deploying PostgreSQL in a structured and consistent manner while allowing the user flexibility in configuration. It simplifies deployment, scaling, configuration and management of PostgreSQL in production at scale in a reliable way.

## Project and community
Charmed PostgreSQL K8s is an official distribution of PostgreSQL. It’s an open-source project that welcomes community contributions, suggestions, fixes and constructive feedback.
- [Read our Code of Conduct](https://ubuntu.com/community/code-of-conduct)
- [Join the Discourse forum](https://discourse.charmhub.io/tag/postgresql)
- Contribute and report bugs to [machine](https://github.com/canonical/postgresql-operator) and [K8s](https://github.com/canonical/postgresql-k8s-operator) operators

## In this documentation
| | |
|--|--|
|  [Tutorial](/t/charmed-postgresql-k8s-tutorial-overview/9296?channel=14/stable) </br>  Get started - a hands-on introduction to using Charmed PostgreSQL K8s operator for new users </br> |  [How-to guides](/t/charmed-postgresql-k8s-how-to-manage-units/9592?channel=14/stable) </br> Step-by-step guides covering key operations and common tasks |
| [Reference](https://charmhub.io/postgresql-k8s/actions?channel=14/stable) </br> Technical information - specifications, APIs, architecture | [Explanation](/t/charmed-postgresql-k8s-explanations-interfaces-endpoints/10252?channel=14/stable) </br> Concepts - discussion and clarification of key topics  |

# Navigation

| Level | Path                          | Navlink                                                                                                           |
| ----- |-------------------------------|-------------------------------------------------------------------------------------------------------------------|
| 1 | tutorial                      | [Tutorial]()                                                                                                      |
| 2 | t-overview                    | [1. Introduction](/t/charmed-postgresql-k8s-tutorial-overview/9296)                                               |
| 2 | t-setup-environment           | [2. Set up the environment](/t/charmed-postgresql-k8s-tutorial-setup-environment/9297)                            |
| 2 | t-deploy-postgresql           | [3. Deploy PostgreSQL](/t/charmed-postgresql-k8s-tutorial-deploy/9298)                                            |
| 2 | t-managing-units              | [4. Manage your units](/t/charmed-postgresql-k8s-tutorial-managing-units/9299)                                    |
| 2 | t-manage-passwords            | [5. Manage passwords](/t/charmed-postgresql-k8s-tutorial-manage-passwords/9300)                                   |
| 2 | t-integrations                | [6. Relate your PostgreSQL to other applications](/t/charmed-postgresql-k8s-tutorial-integrations/9301)           |
| 2 | t-enable-security             | [7. Enable security](/t/charmed-postgresql-k8s-tutorial-enable-security/9302)                                     |
| 2 | t-cleanup-environment         | [8. Cleanup your environment](/t/charmed-postgresql-k8s-tutorial-cleanup/9303)                                    |
| 1 | how-to                        | [How To]()                                                                                                        |
| 2 | h-manage-units                | [Manage units](/t/charmed-postgresql-k8s-how-to-manage-units/9592)                                                |
| 2 | h-enable-encryption           | [Enable encryption](/t/charmed-postgresql-k8s-how-to-enable-encryption/9593)                                      |
| 2 | h-manage-app                  | [Manage applications](/t/charmed-postgresql-k8s-how-to-manage-applications/9594)                                  |
| 2 | h-configure-s3-aws                | [Configure S3 AWS](/t/charmed-postgresql-k8s-how-to-configure-s3-for-aws/9595)                                                |
| 2 | h-configure-s3-radosgw                | [Configure S3 RadosGW](/t/charmed-postgresql-k8s-how-to-configure-s3-for-radosgw/10316)                                                |
| 2 | h-create-and-list-backups     | [Create and List Backups](/t/charmed-postgresql-k8s-how-to-create-and-list-backups/9596)                          |
| 2 | h-restore-backup              | [Restore a Backup](/t/charmed-postgresql-k8s-how-to-restore-backups/9597)                                         |
| 2 | h-migrate-cluster-via-restore | [Cluster Migration with Restore](/t/charmed-postgresql-k8s-how-to-migrate-clusters/9598)                          |
| 1 | reference                     | [Reference]()                                                                                                     |
| 2 | r-actions                     | [Actions](https://charmhub.io/postgresql-k8s/actions)                                                             |
| 2 | r-configurations              | [Configurations](https://charmhub.io/postgresql-k8s/configure)                                                    |
| 2 | r-libraries                   | [Libraries](https://charmhub.io/postgresql-k8s/libraries)                                                 |
| 2 | r-integrations                   | [Integrations](https://charmhub.io/postgresql-k8s/integrations)                                                 |
| 1 | explanation                     | [Explanation]()                                                                                                     |
| 2 | e-interfaces                       | [Interfaces/endpoints](/t/charmed-postgresql-k8s-explanations-interfaces-endpoints/10252)                                                   |
| 2 | e-charm                       | [Charm flowcharts](/t/charmed-postgresql-k8s-reference-charm-api/9305)                                                   |
| 2 | e-peers                       | [Relations flowcharts](/t/charmed-postgresql-k8s-reference-peer-relation/9306)                                           |
| 2 | e-backups                  | [Backups flowcharts](/t/charmed-postgresql-k8s-explanations-backup-flowcharts/10248)                                           |

# Redirects

[details=Mapping table]
| Path | Location |
| ---- | -------- |
[/details]