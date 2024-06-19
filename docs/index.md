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
