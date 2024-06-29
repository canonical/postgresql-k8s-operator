>Reference > Release Notes > [All revisions](/t/11872) > Revision 280/281
# Revision 280/281

<sub>June 28, 2024</sub>

Dear community,

We'd like to announce that Canonical's newest Charmed PostgreSQL K8s operator has been published in the '14/stable' [channel](https://charmhub.io/postgresql-k8s?channel=14/stable) :tada:

|   |AMD64|ARM64|
|---:|:---:|:---:|
| Revision: | 281 | 280 |

[note]
If you are jumping over several stable revisions, make sure to check [previous release notes](/t/11872) before upgrading to this revision.
[/note]  

## Features you can start using today

* [PostgreSQL upgrade 14.10 → 14.11](https://www.postgresql.org/docs/release/14.11/) [[PR#432](https://github.com/canonical/postgresql-operator/pull/432)]
  * [check official PostgreSQL release notes!](https://www.postgresql.org/docs/release/14.11/)
* [New ARM support](https://charmhub.io/postgresql-k8s/docs/r-requirements) [[PR#408](https://github.com/canonical/postgresql-k8s-operator/pull/408)]
* [Cross-region asynchronous replication](https://charmhub.io/postgresql-k8s/docs/h-async-setup) [[PR#447](https://github.com/canonical/postgresql-k8s-operator/pull/447)][[DPE-2897](https://warthogs.atlassian.net/browse/DPE-2897)]
* [Add Differential+Incremental backup support](/t/9596) [[PR#487](https://github.com/canonical/postgresql-k8s-operator/pull/487)][[PR#476](https://github.com/canonical/postgresql-k8s-operator/pull/476)][[DPE-4464](https://warthogs.atlassian.net/browse/DPE-4464)]
* [Add retention time for full backups](https://charmhub.io/s3-integrator/configuration?channel=latest/edge#experimental-delete-older-than-days) [[PR#477](https://github.com/canonical/postgresql-k8s-operator/pull/477)][[DPE-4401](https://warthogs.atlassian.net/browse/DPE-4401)]
* [Added TimescaleDB plugin/extension](https://charmhub.io/postgresql-k8s/configuration?channel=14/candidate#plugin_timescaledb_enable) [[PR#488](https://github.com/canonical/postgresql-k8s-operator/pull/488)]
* [Performance testing with sysbench](https://charmhub.io/sysbench) [[DPE-2852](https://warthogs.atlassian.net/browse/DPE-2852)]
* Internal disable operator mode [[DPE-2470](https://warthogs.atlassian.net/browse/DPE-2470)]
* Users are informed about missing `--trust` flag [[PR#440](https://github.com/canonical/postgresql-k8s-operator/pull/440)][[DPE-3885](https://warthogs.atlassian.net/browse/DPE-3885)]
* Add [experimental_max_connections](https://charmhub.io/postgresql-k8s/configuration?channel=14/candidate#experimental_max_connections) config in [[PR#500](https://github.com/canonical/postgresql-k8s-operator/pull/500)][[DPE-4571]()]
* All the functionality from [previous revisions](https://charmhub.io/postgresql-k8s/docs/r-releases)

## Bugfixes

* Fixed large objects ownership in [PR#390](https://github.com/canonical/postgresql-k8s-operator/pull/390),  [[DPE-3551](https://warthogs.atlassian.net/browse/DPE-3551)]
* Fixed shared buffers validation in [PR#396](https://github.com/canonical/postgresql-k8s-operator/pull/396), [[DPE-3594](https://warthogs.atlassian.net/browse/DPE-3594)]
* Fixed handling S3 relation in primary non-leader unit in [PR#375](https://github.com/canonical/postgresql-k8s-operator/pull/375), [[DPE-3349](https://warthogs.atlassian.net/browse/DPE-3349)]
* Stabilized SST and network cut tests in [PR#385](https://github.com/canonical/postgresql-k8s-operator/pull/385), [[DPE-3473](https://warthogs.atlassian.net/browse/DPE-3473)]
* Fixed pod reconciliation: rerender config/service on pod recreation in [PR#461](https://github.com/canonical/postgresql-k8s-operator/pull/461), [[DPE-2671](https://warthogs.atlassian.net/browse/DPE-2671)]
* Updated `data-platform-libs`: `data_interfaces` to 34 and upgrade to 16 in [PR#454](https://github.com/canonical/postgresql-k8s-operator/pull/454)
* Updated Python dependencies [PR#443](https://github.com/canonical/postgresql-k8s-operator/pull/443)
* Unified juju2 and juju3 test suites [PR#462](https://github.com/canonical/postgresql-k8s-operator/pull/462)
* Converted all tests from unittest to pytest + reenabled secrets everywhere in [PR#452](https://github.com/canonical/postgresql-k8s-operator/pull/452), [[DPE-4068](https://warthogs.atlassian.net/browse/DPE-4068)]
* Added `check_tls_replication` for checking replicas encrypted connection in [PR#444](https://github.com/canonical/postgresql-k8s-operator/pull/444)
* Fixed Primary status message after cluster bootstrap in [PR#435](https://github.com/canonical/postgresql-k8s-operator/pull/435)
* Improved error message on temporary impossible upgrade in [PR#432](https://github.com/canonical/postgresql-k8s-operator/pull/432), [[DPE-3803](https://warthogs.atlassian.net/browse/DPE-3803)]
* Check user existence after relation broken for `db` and `db-admin` interfaces in [PR#425](https://github.com/canonical/postgresql-k8s-operator/pull/425)
* Switched to ruff formatter in [PR#424](https://github.com/canonical/postgresql-k8s-operator/pull/424)
* Updated charm libs and switch away from psycopg2-binary [PR#406](https://github.com/canonical/postgresql-k8s-operator/pull/406)
* Fixed support CPU in millis in [PR#410](https://github.com/canonical/postgresql-k8s-operator/pull/410), [[DPE-3695](https://warthogs.atlassian.net/browse/DPE-3695)]
* Block on legacy roles request (suported by modern interface only) in [PR#391](https://github.com/canonical/postgresql-k8s-operator/pull/391), [[DPE-3099](https://warthogs.atlassian.net/browse/DPE-3099)]
* Avoid SQL queries passwords exposing in postgresql logs in [PR#506](https://github.com/canonical/postgresql-k8s-operator/pull/506), [[DPE-4369](https://warthogs.atlassian.net/browse/DPE-4369)]
* Async replication UX improvements in [PR#491](https://github.com/canonical/postgresql-k8s-operator/pull/491), [[DPE-4256](https://warthogs.atlassian.net/browse/DPE-4256)]
* Address main instability sources on backups integration tests in [PR#496](https://github.com/canonical/postgresql-k8s-operator/pull/496), [[DPE-4427](https://warthogs.atlassian.net/browse/DPE-4427)]
* Always check peer data for legacy secrets in [PR#466](https://github.com/canonical/postgresql-k8s-operator/pull/466)
* Use TLS CA chain for backups in [PR#493](https://github.com/canonical/postgresql-k8s-operator/pull/493), [[DPE-4413](https://warthogs.atlassian.net/browse/DPE-4413)]
* Fix scale up with S3 and TLS relations in [PR#489](https://github.com/canonical/postgresql-k8s-operator/pull/489), [[DPE-4456](https://warthogs.atlassian.net/browse/DPE-4456)]
* Reset active status when removing extensions dependency block [PR#481](https://github.com/canonical/postgresql-k8s-operator/pull/481), [[DPE-4336](https://warthogs.atlassian.net/browse/DPE-4336)]
* Fix secret label in [PR#472](https://github.com/canonical/postgresql-k8s-operator/pull/472), [[DPE-4296](https://warthogs.atlassian.net/browse/DPE-4296)]

Canonical Data issues are now public on both [Jira](https://warthogs.atlassian.net/jira/software/c/projects/DPE/issues/) and [GitHub](https://github.com/canonical/postgresql-k8s-operator/issues) platforms.  
[GitHub Releases](https://github.com/canonical/postgresql-k8s-operator/releases) provide a detailed list of bugfixes, PRs, and commits for each revision.  

## Inside the charms

* Charmed PostgreSQL ships the **PostgreSQL**  - `14.11-0ubuntu0.22.04.1`
* PostgreSQL cluster manager **Patroni** - `3.1.2`
* Backup tools **pgBackRest** - `2.48`
* The Prometheus **postgres_exporter** - `0.12.1-0ubuntu0.22.04.1~ppa1`
* This charm uses [ROCK OCI](https://github.com/orgs/canonical/packages?tab=packages&q=charmed) based on SNAP revision `113`
* This charm ships the latest base `Ubuntu LTS 22.04.4`  

## Technical notes

* Upgrade to this revision (`juju refresh`) is possible from the revision 193+
* It is recommended to use this operator together with modern [Charmed PgBouncer K8s operator](https://charmhub.io/pgbouncer-k8s?channel=1/stable)
* Please check [the external components requirements](https://charmhub.io/postgresql-k8s/docs/r-requirements)
* Please check [previously posted restrictions](https://charmhub.io/postgresql-k8s/docs/r-releases)  
* Ensure [the charm requirements](/t/11744) met

## Contact us

Charmed PostgreSQL K8s is an open source project that warmly welcomes community contributions, suggestions, fixes, and constructive feedback.  
* Raise software issues or feature requests on [**GitHub**](https://github.com/canonical/postgresql-k8s-operator/issues)  
*  Report security issues through [**Launchpad**](https://wiki.ubuntu.com/DebuggingSecurity#How%20to%20File)  
* Contact the Canonical Data Platform team through our [Matrix](https://matrix.to/#/#charmhub-data-platform:ubuntu.com) channel.