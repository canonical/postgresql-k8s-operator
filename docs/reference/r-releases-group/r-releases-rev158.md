>Reference > Release Notes > [All revisions](/t/11872) > [Revision 158](/t/11874)
# Revision 158
<sub>Wednesday, October 18, 2023</sub>

Dear community,

We'd like to announce that Canonical's newest Charmed PostgreSQL operator for Kubernetes has been published in the `14/stable` [channel](https://charmhub.io/postgresql-k8s?channel=14/stable). :tada: 

If you are jumping over several stable revisions, make sure to check [previous release notes](/t/11872) before upgrading to this revision.

## Features you can start using today
* [Add Juju 3 support](/t/11744) (Juju 2 is still supported) [[DPE-1758](https://warthogs.atlassian.net/browse/DPE-1758)]
* All secrets are now stored in [Juju secrets](https://juju.is/docs/juju/manage-secrets) [[DPE-1758](https://warthogs.atlassian.net/browse/DPE-1758)]
* Charm [minor upgrades](/t/12095) and [minor rollbacks](/t/12096) [[DPE-1767](https://warthogs.atlassian.net/browse/DPE-1767)]
* [Canonical Observability Stack (COS)](https://charmhub.io/topics/canonical-observability-stack) support [[DPE-1775](https://warthogs.atlassian.net/browse/DPE-1775)]
* [PostgreSQL plugins support](/t/10945) [[DPE-1372](https://warthogs.atlassian.net/browse/DPE-1372)]
* [Profiles configuration](/t/11975) support [[DPE-2656](https://warthogs.atlassian.net/browse/DPE-2656)]
* [Logs rotation](/t/12098) [[DPE-1755](https://warthogs.atlassian.net/browse/DPE-1755)]
* Workload updated to [PostgreSQL 14.9](https://www.postgresql.org/docs/14/release-14-9.html) [[PR#18](https://github.com/canonical/charmed-postgresql-snap/pull/18)]
* Add '`admin`' [extra user role](https://github.com/canonical/postgresql-k8s-operator/pull/201) [[DPE-2167](https://warthogs.atlassian.net/browse/DPE-2167)]
* New charm '[PostgreSQL Test App](https://charmhub.io/postgresql-test-app)'
* New documentation:
  * [Architecture (HLD/LLD)](/t/11856)
  * [Upgrade section](/t/12092)
  * [Release Notes](/t/11872)
  * [Requirements](/t/11744)
  * [Profiles](/t/11975)
  * [Users](/t/10843)
  * [Logs](/t/12098)
  * [Statuses](/t/11855)
  * [Development](/t/11851)
  * [Testing reference](/t/11774)
  * [Legacy charm](/t/11013)
  * [Plugins/extensions](/t/10907), [supported](/t/10945)
  * [Juju 2.x vs 3.x hints](/t/11986)
  * [Contacts](/t/11852)
* All the functionality from [the previous revisions](/t/11873)

## Bugfixes

Canonical Data issues are now public on both [Jira](https://warthogs.atlassian.net/jira/software/c/projects/DPE/issues/) and [GitHub](https://github.com/canonical/postgresql-k8s-operator/issues) platforms.<br/>[GitHub Releases](https://github.com/canonical/postgresql-k8s-operator/releases) provide a detailed list of bugfixes/PRs/Git commits for each revision.<br/>Highlights for the current revision:

* [DPE-1470](https://warthogs.atlassian.net/browse/DPE-1470), [DPE-2419](https://warthogs.atlassian.net/browse/DPE-2419) Fixed K8s resources cleanup
* [DPE-1584](https://warthogs.atlassian.net/browse/DPE-1584) Backup/restore stabilization bugfixes
* [DPE-2546](https://warthogs.atlassian.net/browse/DPE-2546) Split stanza create and stanza check (backup stabilization)
* [DPE-2626](https://warthogs.atlassian.net/browse/DPE-2626), [DPE-2627](https://warthogs.atlassian.net/browse/DPE-2627) Create bucket once and clear up blocked statuses (backup stabilization)
* [DPE-2657](https://warthogs.atlassian.net/browse/DPE-2657) Fix replication after restore
* [DPE-1590](https://warthogs.atlassian.net/browse/DPE-1590) Fixed deployment on old microk8s (e.g. 1.22)
* [DPE-2193](https://warthogs.atlassian.net/browse/DPE-2193) Fixed databases access to requested db only 
* [DPE-1999](https://warthogs.atlassian.net/browse/DPE-1999) Fixed TLS race condition in new relations (stuck in 'awaiting for cluster to start'/'awaiting for member to start')
* [DPE-2338](https://warthogs.atlassian.net/browse/DPE-2338) Use SCRAM by default
* [DPE-2616](https://warthogs.atlassian.net/browse/DPE-2616) Auto-tune profile `production` (mimic defaults of [the legacy charm](/t/11013))
* [DPE-2569](https://warthogs.atlassian.net/browse/DPE-2569) Set waiting status while extensions are being enabled
* [DPE-2015](https://warthogs.atlassian.net/browse/DPE-2015), [DPE-2044](https://warthogs.atlassian.net/browse/DPE-2044) Add missing zoneinfo

## Inside the charms
* Charmed PostgreSQL K8s ships the latest PostgreSQL “14.9-0ubuntu0.22.04.1”
* PostgreSQL cluster manager Patroni updated to "3.0.2"
* Backup tools pgBackRest updated to "2.47"
* The Prometheus postgres-exporter is "0.12.1-0ubuntu0.22.04.1~ppa1"
* K8s charms [based on our](https://github.com/orgs/canonical/packages?tab=packages&q=charmed) ROCK OCI (Ubuntu LTS “22.04” - ubuntu:22.04-based)
* Principal charms supports the latest LTS series “22.04” only.
* Subordinate charms support LTS “22.04” and “20.04” only.

## Technical notes

* `juju refresh` from the old-stable revision 73 to the current-revision 158 is **NOT** supported!!!<br/>The [upgrade](/t/12092) functionality is new and supported for revision 158+ only!
* Please check [the external components requirements](/t/11744)
* Please check additionally [the previously posted restrictions](/t/11873)
* Ensure [the charm requirements](/t/11744) met

## Contact us

Charmed PostgreSQL K8s is an open source project that warmly welcomes community contributions, suggestions, fixes, and constructive feedback.

* Raise software issues or feature requests on [**GitHub**](https://github.com/canonical/postgresql-k8s-operator/issues/new/choose)
* Report security issues through [**Launchpad**](https://wiki.ubuntu.com/DebuggingSecurity#How%20to%20File)
* Contact the Canonical Data Platform team through our [Matrix](https://matrix.to/#/#charmhub-data-platform:ubuntu.com) channel.