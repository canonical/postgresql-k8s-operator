>Reference > Release Notes > [All revisions](/t/11872) > [Revision 193](/t/13208)
# Revision 193
<sub>March 13, 2024</sub>

Dear community,

We'd like to announce that Canonical's newest Charmed PostgreSQL operator for Kubernetes has been published in the `14/stable` [channel](https://charmhub.io/postgresql-k8s?channel=14/stable). :tada: 

If you are jumping over several stable revisions, make sure to check [previous release notes](/t/11872) before upgrading to this revision.

## Features you can start using today
* [CORE] PostgreSQL upgrade 14.9 -> 14.10. ([DPE-3217](https://warthogs.atlassian.net/browse/DPE-3217))
  * **Note**: It is advisable to REINDEX potentially-affected indexes after installing this update! (See [PostgreSQL14.10 changelog](https://changelogs.ubuntu.com/changelogs/pool/main/p/postgresql-14/postgresql-14_14.10-0ubuntu0.22.04.1/changelog))
* [CORE] Juju 3.1.7+ support ([#2037120](https://bugs.launchpad.net/juju/+bug/2037120))
* [PLUGINS] pgVector extension/plugin ([DPE-3159](https://warthogs.atlassian.net/browse/DPE-3159))
* [PLUGINS] New PostGIS plugin ([#363](https://github.com/canonical/postgresql-k8s-operator/pull/363))
* [PLUGINS] More new plugins - [50 in total](/t/10945)
* [MONITORING] COS Awesome Alert rules ([DPE-3161](https://warthogs.atlassian.net/browse/DPE-3161))
* [SECURITY] Updated TLS libraries for compatibility with new charms
  * [manual-tls-certificates](https://charmhub.io/manual-tls-certificates)
  * [self-signed-certificates](https://charmhub.io/self-signed-certificates)
  * Any charms compatible with [ tls_certificates_interface.v2.tls_certificates](https://charmhub.io/tls-certificates-interface/libraries/tls_certificates)
* All functionality from [previous revisions](/t/11872)

## Bugfixes
* Stabilized internal Juju secrets management ([DPE-3199](https://warthogs.atlassian.net/browse/DPE-3199) | [#358](https://github.com/canonical/postgresql-k8s-operator/pull/358))
* Check system identifier in stanza (backups setup stabilization) ([DPE-3061](https://warthogs.atlassian.net/browse/DPE-3061))

Canonical Data issues are now public on both [Jira](https://warthogs.atlassian.net/jira/software/c/projects/DPE/issues/) and [GitHub](https://github.com/canonical/postgresql-k8s-operator/issues) platforms.
[GitHub Releases](https://github.com/canonical/postgresql-k8s-operator/releases) provide a detailed list of bugfixes, PRs, and commits for each revision.

## Inside the charms

* Charmed PostgreSQL ships the **PostgreSQL** `14.10-0ubuntu0.22.04.1`
* PostgreSQL cluster manager **Patroni** - `v.3.1.2`
* Backup tools **pgBackRest** - `v.2.48`
* The Prometheus **postgres_exporter** is `0.12.1-0ubuntu0.22.04.1~ppa1`
* This charm uses [ROCK OCI](https://github.com/orgs/canonical/packages?tab=packages&q=charmed)  based on SNAP revision 96
* This charm ships the latest base `Ubuntu LTS 22.04.3`

## Technical notes

* Starting with this revision (193+), you can use `juju refresh` to upgrade Charmed PostgreSQL K8s
* It is recommended to use this operator together with modern [Charmed PgBouncer operator](https://charmhub.io/pgbouncer-k8s?channel=1/stable)
* Please check [the external components requirements](/t/11744)
* Please check [the previously posted restrictions](/t/11872)
* Ensure [the charm requirements](/t/11744) met

## Contact us

Charmed PostgreSQL K8s is an open source project that warmly welcomes community contributions, suggestions, fixes, and constructive feedback.

* Raise software issues or feature requests on [**GitHub**](https://github.com/canonical/postgresql-k8s-operator/issues/new/choose)
* Report security issues through [**Launchpad**](https://wiki.ubuntu.com/DebuggingSecurity#How%20to%20File)
* Contact the Canonical Data Platform team through our [Matrix](https://matrix.to/#/#charmhub-data-platform:ubuntu.com) channel.