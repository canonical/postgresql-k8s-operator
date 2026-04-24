---
relatedlinks: "[Charmhub](https://charmhub.io/postgresql?channel=16/edge)"
---

# Charmed PostgreSQL K8s documentation

```{caution}
**Charmed PostgreSQL K8s 16 is under development.** Please wait for the upcoming stable release before deploying it in production, or see the documentation for [version 14](https://canonical-charmed-postgresql-k8s.readthedocs-hosted.com/14/).

Meanwhile, you’re welcome to explore the [`16/edge` track](https://charmhub.io/postgresql-k8s?channel=16/edge) and share your feedback as we continue to improve.
```

Charmed PostgreSQL K8s is an open-source software operator designed to deploy and operate object-relational databases on Kubernetes. It packages the powerful database management system [PostgreSQL](https://www.postgresql.org/) into a charmed operator for deployment with [Juju](https://juju.is/docs/juju).

This charmed operator meets the need of simplifying deployment, scaling, configuration and management of relational databases in large-scale production environments reliably. It is equipped with several features to securely store and scale complicated data workloads, including easy integration with client applications.
 
Charmed PostgreSQL K8s is made for anyone looking for a comprehensive database management interface, whether for operating a complex production environment or simply as a playground to learn more about databases and charms.

```{note}
This is a **Kubernetes** operator. To deploy on IAAS/VM, see [Charmed PostgreSQL VM](https://canonical-charmed-postgresql.readthedocs-hosted.com/).
```

## In this documentation

### Get started

Learn about what's in the charm, how to set up your environment, and perform the most common operations.

* **Charm overview**: {ref}`system-requirements` • {ref}`architecture` 
* **Deploy PostgreSQL**: {ref}`Guided tutorial <tutorial>` • {ref}`deploy-quickstart` • {ref}`Set up a cloud <deploy-clouds>`
* **Key operations**: {ref}`Scale your cluster <scale-replicas>` • {ref}`Connect to a client <integrate-with-another-application>` • {ref}`Create a backup <create-a-backup>`

### Production deployments

Advanced deployments and operations focused on production scenarios and high availability.

* **Advanced deployment scenarios**: {ref}`Terraform <terraform>` • {ref}`Air-gapped deployments <air-gapped>` • {ref}`Multiple availability zones <multi-az>` • {ref}`Cluster-cluster replication <cross-regional-async-replication>` • {ref}`Logical replication <logical-replication>`
* **Networking**: {ref}`Enable TLS encryption <enable-tls>` • {ref}`External network access <external-network-access>`
* **Troubleshooting**: {ref}`Overview and tools <troubleshooting>` • {ref}`Logs<logs>`

### Charm developers

Information for developers looking to make their application compatible with PostgreSQL.

* **Charm integrations**: {ref}`Interfaces and endpoints <interfaces-and-endpoints>` • {ref}`How to integrate with your charm with PostgreSQL <integrate-with-your-charm>`
* **Learn about the PostgreSQL charm's design**: {ref}`architecture` • {ref}`Internal users <users>` • {ref}`Roles <roles>`
* **Juju properties**: [Configuration parameters](https://charmhub.io/postgresql-k8s/configurations?channel=16/stable) • [Actions](https://charmhub.io/postgresql-k8s/actions?channel=16/stable)

## How this documentation is organised

This documentation uses the [Diátaxis documentation structure](https://diataxis.fr/):

* The {ref}`tutorial` provides step-by-step guidance for a beginner through the basics of a deployment in a local machine.
* {ref}`how-to` are more focused, and assume you already have basic familiarity with the product.
* {ref}`reference` contains structured information for quick lookup, such as system requirements and configuration parameters
* {ref}`explanation` gives more background and context about key topics

## Project and community

Charmed PostgreSQL is an official distribution of PostgreSQL. It’s an open-source project that welcomes community contributions, suggestions, fixes and constructive feedback.

### Get involved

* [Discourse forum](https://discourse.charmhub.io/tag/postgresql)
* [Public Matrix channel](https://matrix.to/#/#charmhub-data-platform:ubuntu.com)
* [Report an issue](https://github.com/canonical/postgresql-k8s-operator/issues/new/choose)
* [Contribute](https://github.com/canonical/postgresql-k8s-operator/blob/16/edge/CONTRIBUTING.md)

### Governance and policies

- [Code of Conduct](https://ubuntu.com/community/code-of-conduct)


```{toctree}
:titlesonly:
:hidden:

tutorial
how-to/index
reference/index
explanation/index
```