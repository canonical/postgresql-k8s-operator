>Reference > Release Notes > [All revisions](/t/11872) > [Revision 177](/t/12668)

# Revision 177
<sub>January 3, 2024</sub>

Dear community,

We'd like to announce that Canonical's newest Charmed PostgreSQL operator for Kubernetes has been published in the `14/stable` [channel](https://charmhub.io/postgresql-k8s?channel=14/stable). :tada: 

If you are jumping over several stable revisions, make sure to check [previous release notes](/t/11875) before upgrading to this revision.

## Features you can start using today

* [Core] Updated `Charmed PostgreSQL` ROCK image in ([PR#336](https://github.com/canonical/postgresql-k8s-operator/pull/336))([DPE-3039](https://warthogs.atlassian.net/browse/DPE-3039)):
  * `Patroni` updated from 3.0.2 to 3.1.2
  * `Pgbackrest` updated from 2.47 to 2.48
* [Plugins] [Add 24 new plugins/extension](https://charmhub.io/postgresql-k8s/docs/r-plugins-extensions) in ([PR#294](https://github.com/canonical/postgresql-k8s-operator/pull/294))
* [Plugins] **NOTE**:  extension `plpython3u` is deprecated and will be removed from [list of supported plugins](/t/10945) soon!
* [Config] [Add 29 new configuration options](https://charmhub.io/postgresql-k8s/configure) in ([PR#281](https://github.com/canonical/postgresql-k8s-operator/pull/281))([DPE-1782](https://warthogs.atlassian.net/browse/DPE-1782))
* [Config] **NOTE:** the config option `profile-limit-memory` is deprecated. Use `profile_limit_memory` (to follow the [naming conventions](https://juju.is/docs/sdk/naming))! ([PR#348](https://github.com/canonical/postgresql-k8s-operator/pull/348))([DPE-3095](https://warthogs.atlassian.net/browse/DPE-3095))
* [Charm] Add Juju Secret labels in ([PR#303](https://github.com/canonical/postgresql-k8s-operator/pull/303))([DPE-2838](https://warthogs.atlassian.net/browse/DPE-2838))
* [Charm] Update Python dependencies in ([PR#315](https://github.com/canonical/postgresql-k8s-operator/pull/315))([PR#318](https://github.com/canonical/postgresql-k8s-operator/pull/318))
* [DB] Add handling of tables ownership in ([PR#334](https://github.com/canonical/postgresql-k8s-operator/pull/334))([DPE-2740](https://warthogs.atlassian.net/browse/DPE-2740))
* [[COS](https://charmhub.io/topics/canonical-observability-stack)] Moved Grafana dashboard legends to the bottom of the graph in ([PR#337](https://github.com/canonical/postgresql-k8s-operator/pull/337))([DPE-2622](https://warthogs.atlassian.net/browse/DPE-2622))
* [CI/CD] Charm migrated to GitHub Data reusable workflow in ([PR#338](https://github.com/canonical/postgresql-k8s-operator/pull/338))([DPE-3064](https://warthogs.atlassian.net/browse/DPE-3064))
* All the functionality from [the previous revisions](/t/11872)

## Bugfixes

Canonica Data issues are now public on both [Jira](https://warthogs.atlassian.net/jira/software/c/projects/DPE/issues/) and [GitHub](https://github.com/canonical/postgresql-k8s-operator/issues) platforms.<br/>[GitHub Releases](https://github.com/canonical/postgresql-k8s-operator/releases) provide a detailed list of bugfixes/PRs/Git commits for each revision.

* Fixed handle scaling to zero units in ([PR#331](https://github.com/canonical/postgresql-k8s-operator/pull/331))([DPE-2728](https://warthogs.atlassian.net/browse/DPE-2728))
* Fixed plugins enabling performance by toggling all plugins in one go ([PR#322](https://github.com/canonical/postgresql-k8s-operator/pull/322))([DPE-2903](https://warthogs.atlassian.net/browse/DPE-2903))
* Fixed enabling extensions when new database is created in ([PR#290](https://github.com/canonical/postgresql-k8s-operator/pull/290))([DPE-2569](https://warthogs.atlassian.net/browse/DPE-2569))
* Fixed locales availability in ROCK ([PR#291](https://github.com/canonical/postgresql-k8s-operator/pull/291))

## Inside the charms

* Charmed PostgreSQL K8s ships the latest PostgreSQL “14.9-0ubuntu0.22.04.1”
* PostgreSQL cluster manager Patroni updated to "3.2.1"
* Backup tools pgBackRest updated to "2.48"
* The Prometheus postgres-exporter is "0.12.1-0ubuntu0.22.04.1~ppa1"
* K8s charms [based on our](https://github.com/orgs/canonical/packages?tab=packages&q=charmed) ROCK OCI (Ubuntu LTS “22.04” - ubuntu:22.04-based) based on SNAP revision 89
* Principal charms supports the latest LTS series “22.04” only
* Subordinate charms support LTS “22.04” and “20.04” only

## Technical notes:

* Upgrade (`juju refresh`) is possible from this revision 158+
* Use this operator together with a modern operator "[pgBouncer K8s](https://charmhub.io/pgbouncer-k8s)"
* Please check [the external components requirements](/t/11744)
* Please check additionally [the previously posted restrictions](/t/11872)
* Ensure [the charm requirements](/t/11744) met

## Contact us

Charmed PostgreSQL K8s is an open source project that warmly welcomes community contributions, suggestions, fixes, and constructive feedback.

* Raise software issues or feature requests on [**GitHub**](https://github.com/canonical/postgresql-k8s-operator/issues/new/choose)
* Report security issues through [**Launchpad**](https://wiki.ubuntu.com/DebuggingSecurity#How%20to%20File)
* Contact the Canonical Data Platform team through our [Matrix](https://matrix.to/#/#charmhub-data-platform:ubuntu.com) channel.