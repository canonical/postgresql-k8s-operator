>Reference > Release Notes > [All revisions](/t/11872) > [Revision 73](/t/11873)
# Revision 73
<sub>Thursday, April 20, 2023</sub>

Dear community,

We'd like to announce that Canonical's newest Charmed PostgreSQL operator for Kubernetes has been published in the `14/stable` [channel](https://charmhub.io/postgresql-k8s?channel=14/stable). :tada: 

If you are jumping over several stable revisions, make sure to check [previous release notes](/t/11872) before upgrading to this revision.

## Features you can start using today

* Deploying on Kubernetes (tested with MicroK8s, GKE)
* Scaling up/down in one simple juju command
* HA using [Patroni](https://github.com/zalando/patroni)
* Full backups and restores are supported when using any S3-compatible storage
* TLS support (using “[tls-certificates](https://charmhub.io/tls-certificates-operator)” operator)
* DB access outside of Juju using “[data-integrator](https://charmhub.io/data-integrator)”
* Data import using standard tools e.g. “[PostgreSQL Data Injector](https://charmhub.io/postgresql-data-k8s)”
* [Documentation](https://charmhub.io/postgresql-k8s?channel=14/stable)
<!--
|Charm|Version|Charm channel|Documentation|License|
| --- | --- | --- | --- | --- |
|[PostgreSQL K8s](https://github.com/canonical/postgresql-k8s-operator)|14.7|[14/stable](https://charmhub.io/postgresql-k8s?channel=14/stable) (r73)|[Tutorial](https://charmhub.io/postgresql-k8s/docs/t-overview?channel=14/edge), [Readme](https://github.com/canonical/postgresql-k8s-operator/blob/main/README.md), [Contributing](https://github.com/canonical/postgresql-k8s-operator/blob/main/CONTRIBUTING.md)|[Apache 2.0](https://github.com/canonical/postgresql-k8s-operator/blob/main/LICENSE)|
-->
## Inside the charms

* Charmed PostgreSQL K8s charm ships the latest PostgreSQL “14.7-0ubuntu0.22.04.1”
* K8s charms [based on our](https://github.com/orgs/canonical/packages?tab=packages&q=charmed) ROCK OCI (Ubuntu LTS “22.04” - ubuntu:22.04-based)
* Principal charms supports the latest LTS series “22.04” only.
* Subordinate charms support LTS “22.04” and “20.04” only.

## Technical notes

Compatibility with legacy charms:
 * The new PostgreSQL charm is also a juju interface-compatible replacement for legacy PostgreSQL charms (using legacy interface `pgsql`, via endpoints `db` and `db-admin`).
However, **it is highly recommended to migrate to the modern interface [`postgresql_client`](https://github.com/canonical/charm-relation-interfaces)** (endpoint `database`).
    * Please [contact us](#heading--contact) if you are considering migrating from other “legacy” charms not mentioned above. 
* Charm PostgreSQL K8s charm follows the SNAP track “14” (through repackaed ROCK/OCI image).
* No “latest” track in use (no surprises in tracking “latest/stable”)!
  * Charmed PostgreSQL K8s charms provide [legacy charm](/t/11013) through “latest/stable”.
* Charm lifecycle flowchart diagrams: [PostgreSQL](https://github.com/canonical/postgresql-k8s-operator/tree/main/docs/reference).
* Modern interfaces are well described in “[Interfaces catalogue](https://github.com/canonical/charm-relation-interfaces)” and implemented by '[data-platform-libs](https://github.com/canonical/data-platform-libs/)'.
* Known limitation: PostgreSQL extensions are not yet supported.

## Contact us

Charmed PostgreSQL K8s is an open source project that warmly welcomes community contributions, suggestions, fixes, and constructive feedback.

* Raise software issues or feature requests on [**GitHub**](https://github.com/canonical/postgresql-k8s-operator/issues/new/choose)
* Report security issues through [**Launchpad**](https://wiki.ubuntu.com/DebuggingSecurity#How%20to%20File)
* Contact the Canonical Data Platform team through our [Matrix](https://matrix.to/#/#charmhub-data-platform:ubuntu.com) channel.

<!--The document was originally posted [here](https://discourse.charmhub.io/t/juju-operators-for-postgresql-and-mysql-are-now-stable/10223).-->