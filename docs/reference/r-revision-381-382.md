>Reference > Release Notes > [All revisions] > Revision 381/382

# Revision 381/382
<sub>September 11, 2024</sub>

Dear community,

Canonical's newest Charmed PostgreSQL K8s operator has been published in the [14/stable channel].

Due to the newly added support for `arm64` architecture, the PostgreSQL K8s charm now releases multiple revisions simultaneously:
* Revision 381 is built for `amd64` on Ubuntu 22.04 LTS
* Revision 382 is built for `arm64` on Ubuntu 22.04 LTS

To make sure you deploy for the right architecture, we recommend setting an [architecture constraint](https://juju.is/docs/juju/constraint#heading--arch) for your entire juju model.

Otherwise, it can be done at deploy time with the `--constraints` flag:
```shell
juju deploy postgresql-k8s --trust --constraints arch=<arch> 
```
where `<arch>` can be `amd64` or `arm64`.

---

## Highlights 

* Upgraded PostgreSQL from v.14.11 → v.14.12 ([PR #563](https://github.com/canonical/postgresql-k8s-operator/pull/563))
  * Check the official [PostgreSQL release notes](https://www.postgresql.org/docs/release/14.12/)
* Added support for Point In Time Recovery ([PR #554](https://github.com/canonical/postgresql-k8s-operator/pull/554)) ([DPE-4839](https://warthogs.atlassian.net/browse/DPE-4839))
* Added COS tracing support with [tempo-k8s](https://charmhub.io/tempo-k8s) ([PR #497](https://github.com/canonical/postgresql-k8s-operator/pull/497)) ([DPE-4617](https://warthogs.atlassian.net/browse/DPE-4617))

## Features
 
* Added user warning when deploying charm with wrong architecture ([PR #613](https://github.com/canonical/postgresql-k8s-operator/pull/613)) ([DPE-4239](https://warthogs.atlassian.net/browse/DPE-4239))
* Improved backups behavior ([PR #542](https://github.com/canonical/postgresql-k8s-operator/pull/542)) ([DPE-4479](https://warthogs.atlassian.net/browse/DPE-4479))
* Add libpq's connection string URI format to `uri` field in relation databag ([PR #545](https://github.com/canonical/postgresql-k8s-operator/pull/545)) ([DPE-2278](https://warthogs.atlassian.net/browse/DPE-2278))
* Changed 'master' to 'primary' in Patroni leader role ([PR #532](https://github.com/canonical/postgresql-k8s-operator/pull/532)) ([DPE-1177](https://warthogs.atlassian.net/browse/DPE-1177))
* Added password to Patroni's REST API ([PR #661](https://github.com/canonical/postgresql-k8s-operator/pull/661)) ([DPE-5275](https://warthogs.atlassian.net/browse/DPE-5275))
* Improve pgbackrest logging ([PR #587](https://github.com/canonical/postgresql-k8s-operator/pull/587))

## Bugfixes and stability

* Restart pebble service if it's down ([PR #581](https://github.com/canonical/postgresql-k8s-operator/pull/581)) ([DPE-4806](https://warthogs.atlassian.net/browse/DPE-4806))
* Switched test app interface ([PR #595](https://github.com/canonical/postgresql-k8s-operator/pull/595))
* Addeded missing `await` to `invalid_extra_user_roles` integration test + fix check loop ([PR #602](https://github.com/canonical/postgresql-k8s-operator/pull/602))
* Fixed UTC time zone ([PR #592](https://github.com/canonical/postgresql-k8s-operator/pull/592))
* Fix PITR test on Juju 2.9 ([PR #596](https://github.com/canonical/postgresql-k8s-operator/pull/596)) ([DPE-4990](https://warthogs.atlassian.net/browse/DPE-4990))
* Fixed storage ownership ([PR #580](https://github.com/canonical/postgresql-k8s-operator/pull/580)) ([DPE-4227](https://warthogs.atlassian.net/browse/DPE-4227))
* Fixed get-password action description ([PR #605](https://github.com/canonical/postgresql-k8s-operator/pull/605)) ([DPE-5019](https://warthogs.atlassian.net/browse/DPE-5019))
* Quick fix for blocked CI ([PR #533](https://github.com/canonical/postgresql-k8s-operator/pull/533))
* CI stability fixes + slicing tests ([PR #524](https://github.com/canonical/postgresql-k8s-operator/pull/524)) ([DPE-4620](https://warthogs.atlassian.net/browse/DPE-4620))
* Added test for relations coherence ([PR #505](https://github.com/canonical/postgresql-k8s-operator/pull/505))
* Addressed test_charm and test_self_healing instabilities ([PR #510](https://github.com/canonical/postgresql-k8s-operator/pull/510)) ([DPE-4594](https://warthogs.atlassian.net/browse/DPE-4594))
* Split PITR backup test in AWS and GCP ([PR #664](https://github.com/canonical/postgresql-k8s-operator/pull/664)) ([DPE-5244](https://warthogs.atlassian.net/browse/DPE-5244))
* Import JujuVersion from ops.jujuversion instead of ops.model ([PR #640](https://github.com/canonical/postgresql-k8s-operator/pull/640))
* Don't block on missing Postgresql version ([PR #626](https://github.com/canonical/postgresql-k8s-operator/pull/626)) ([DPE-3562](https://warthogs.atlassian.net/browse/DPE-3562))
* Run integration tests on arm64 ([PR #478](https://github.com/canonical/postgresql-k8s-operator/pull/478))
* Improved async replication stability ([PR #526](https://github.com/canonical/postgresql-k8s-operator/pull/526)) ([DPE-4736](https://warthogs.atlassian.net/browse/DPE-4736))
* Removed deprecated config option `profile-limit-memory` ([PR #608](https://github.com/canonical/postgresql-k8s-operator/pull/608))
* Pause Patroni in the TLS test ([PR #588](https://github.com/canonical/postgresql-k8s-operator/pull/588)) ([DPE-4533](https://warthogs.atlassian.net/browse/DPE-4533))
* Enforce Juju versions ([PR #544](https://github.com/canonical/postgresql-k8s-operator/pull/544)) ([DPE-4811](https://warthogs.atlassian.net/browse/DPE-4811))
* Block charm if plugin disable fails due to dependent objects ([PR #567](https://github.com/canonical/postgresql-k8s-operator/pull/567)) ([DPE-4801](https://warthogs.atlassian.net/browse/DPE-4801))
* Temporarily disable log forwarding & fix for race in Patroni REST password setup ([PR #663](https://github.com/canonical/postgresql-k8s-operator/pull/663))
* Use manifest file to check for charm architecture ([PR #665](https://github.com/canonical/postgresql-k8s-operator/pull/665)) ([DPE-4239](https://warthogs.atlassian.net/browse/DPE-4239))
* Only write app data if leader ([PR #676](https://github.com/canonical/postgresql-k8s-operator/pull/676)) ([DPE-5325](https://warthogs.atlassian.net/browse/DPE-5325))
* Added log for `fix_leader_annotation` method ([PR #679](https://github.com/canonical/postgresql-k8s-operator/pull/679))

## Known limitations

 * The unit action `resume-upgrade` randomly raises a [harmless error message](https://warthogs.atlassian.net/browse/DPE-5420): `terminated`.
 * The [charm sysbench](https://charmhub.io/sysbench) may [crash](https://warthogs.atlassian.net/browse/DPE-5436) during a PostgreSQL charm refresh.
 * Make sure that [cluster-cluster replication](/t/13895) is requested for the same charm/workload revisions. An automated check is [planned](https://warthogs.atlassian.net/browse/DPE-5419).
 * [Contact us](/t/11852) to schedule [a cluster-cluster replication](/t/13895) upgrade with you.

If you are jumping over several stable revisions, check [previous release notes][All revisions] before upgrading.

## Requirements and compatibility
This charm revision features the following changes in dependencies:
* (increased) The minimum Juju version required to reliably operate **all** features of the release is `v3.4.5`
  > You can upgrade to this revision on Juju  `v2.9.50+`, but it will not support newer features like cross-regional asynchronous replication, point-in-time recovery, and modern TLS certificate charm integrations.
* (increased) PostgreSQL version 14.12

See the [system requirements] for more details about Juju versions and other software and hardware prerequisites.

### Integration tests
Below are the charm integrations tested with this revision on different Juju environments and architectures:
* Juju `v.2.9.50` on `amd64`
* Juju  `v.3.4.5` on `amd64` and `arm64`

#### Juju `v.2.9.50` on `amd64`

| Software | Version |
|-----|-----|
| [tls-certificates-operator] | `rev 22`, `legacy/stable` | 

#### Juju  `v.3.4.5` on `amd64` and `arm64`

| Software | Version | 
|-----|-----|
| [self-signed-certificates] | `rev 155`, `latest/stable` | 

####  All
| Software | Version | 
|-----|-----|
| [microk8s] | `v.1.31`, `strict/stable` | 
| [indico] | `rev 213` | 
| [discourse-k8s] | `rev 124` | 
| [data-integrator] | `rev 41` | 
| [s3-integrator] | `rev 31` | 
| [postgresql-test-app] | `rev 239` | 

See the [`/lib/charms` directory on GitHub] for more details about all supported libraries.

See the [`metadata.yaml` file on GitHub] for a full list of supported interfaces.

## Packaging

This charm is based on the Charmed PostgreSQL K8s [rock image]. It packages:
* [postgresql `v.14.12`]
* [pgbouncer `v.1.21`]
* [patroni `v.3.1.2 `]
* [pgBackRest `v.2.48`]
* [prometheus-postgres-exporter `v.0.12.1`]

## Dependencies and automations
[details=This section contains a list of updates to libs, dependencies, actions, and workflows.] 

* Updated canonical/charming-actions action to v2.6.3 ([PR #673](https://github.com/canonical/postgresql-k8s-operator/pull/673))
* Updated data-platform-workflows to v21.0.1 ([PR #660](https://github.com/canonical/postgresql-k8s-operator/pull/660))
* Updated dependency canonical/microk8s to v1.31 ([PR #632](https://github.com/canonical/postgresql-k8s-operator/pull/632))
* Updated dependency cryptography to v43.0.1([PR #681](https://github.com/canonical/postgresql-k8s-operator/pull/681))
* Updated dependency juju/juju to v2.9.50 ([PR #589](https://github.com/canonical/postgresql-k8s-operator/pull/589))
* Updated dependency juju/juju to v3.4.5 ([PR #599](https://github.com/canonical/postgresql-k8s-operator/pull/599))
* Updated dependency tenacity to v9 ([PR #600](https://github.com/canonical/postgresql-k8s-operator/pull/600))
* Updated ghcr.io/canonical/charmed-postgresql:14.12-22.04_edge Docker digest to 7ef86a3 ([PR #655](https://github.com/canonical/postgresql-k8s-operator/pull/655))
* Updated rock to 14.12 ([PR #563](https://github.com/canonical/postgresql-k8s-operator/pull/563))
* Switch Jira issue sync from workflow to bot ([PR #636](https://github.com/canonical/postgresql-k8s-operator/pull/636))
* Use poetry package-mode=false ([PR #594](https://github.com/canonical/postgresql-k8s-operator/pull/594))
* Updated logging: bump lib and introduce pebble log forwarding ([PR #486](https://github.com/canonical/postgresql-k8s-operator/pull/486))
* Updated postgresql lib ([PR #546](https://github.com/canonical/postgresql-k8s-operator/pull/546))
* Bumped coverage ([PR #623](https://github.com/canonical/postgresql-k8s-operator/pull/623))
* Test service patch lib update ([PR #624](https://github.com/canonical/postgresql-k8s-operator/pull/624))
[/details]

<!-- DISCOURSE TOPICS-->
[All revisions]: /t/11872
[system requirements]: /t/11744

<!-- CHARM GITHUB -->
[`/lib/charms` directory on GitHub]: https://github.com/canonical/postgresql-k8s-operator/tree/main/lib/charms
[`metadata.yaml` file on GitHub]: https://github.com/canonical/postgresql-k8s-operator/blob/main/metadata.yaml

<!-- CHARMHUB -->
[14/stable channel]: https://charmhub.io/postgresql?channel=14/stable

<!-- SNAP/ROCK-->
[`charmed-postgresql` packaging]: https://github.com/canonical/charmed-postgresql-rock
[rock image]: ghcr.io/canonical/charmed-postgresql@sha256:7ef86a352c94e2a664f621a1cc683d7a983fd86e923d98c32b863f717cb1c173 

[postgresql `v.14.12`]: https://launchpad.net/ubuntu/+source/postgresql-14/14.12-0ubuntu0.22.04.1
[pgbouncer `v.1.21`]: https://launchpad.net/~data-platform/+archive/ubuntu/pgbouncer
[patroni `v.3.1.2 `]: https://launchpad.net/~data-platform/+archive/ubuntu/patroni
[pgBackRest `v.2.48`]: https://launchpad.net/~data-platform/+archive/ubuntu/pgbackrest
[prometheus-postgres-exporter `v.0.12.1`]: https://launchpad.net/~data-platform/+archive/ubuntu/postgres-exporter

<!-- EXTERNAL LINKS -->
[juju]: https://juju.is/docs/juju/
[lxd]: https://documentation.ubuntu.com/lxd/en/latest/
[nextcloud]: https://charmhub.io/nextcloud
[mailman3-core]: https://charmhub.io/mailman3-core
[data-integrator]: https://charmhub.io/data-integrator
[s3-integrator]: https://charmhub.io/s3-integrator
[postgresql-test-app]: https://charmhub.io/postgresql-test-app
[discourse-k8s]: https://charmhub.io/discourse-k8s
[indico]: https://charmhub.io/indico
[microk8s]: https://charmhub.io/microk8s
[tls-certificates-operator]: https://charmhub.io/tls-certificates-operator
[self-signed-certificates]: https://charmhub.io/self-signed-certificates

<!-- BADGES (unused) -->
[amd64]: https://img.shields.io/badge/amd64-darkgreen
[arm64]: https://img.shields.io/badge/arm64-blue