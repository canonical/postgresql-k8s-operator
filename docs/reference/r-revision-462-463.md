>Reference > Release Notes > [All revisions] > Revision 462/463

# Revision 462/463
<sub>December 18, 2024</sub>

Canonical's newest Charmed PostgreSQL K8s operator has been published in the `14/stable` channel.

Due to the newly added support for `arm64` architecture, the PostgreSQL charm now releases multiple revisions simultaneously:
* Revision 462 is built for `amd64` on Ubuntu 22.04 LTS
* Revision 463 is built for `arm64` on Ubuntu 22.04 LTS

> See also: [How to perform a minor upgrade]

### Contents
* [Highlights](#highlights)
* [Features and improvements](#features-and-improvements)
* [Bugfixes and maintenance](#bugfixes-and-maintenance)
* [Known limitations](#known-limitations)
* [Requirements and compatibility](#requirements-and-compatibility)
  * [Packaging](#packaging)
---

## Highlights 
* Upgraded PostgreSQL from v.14.12 → v.14.13
  * Check the official [PostgreSQL release notes](https://www.postgresql.org/docs/release/14.13/)
* Added timeline management to point-in-time recovery (PITR) ([PR #716](https://github.com/canonical/postgresql-k8s-operator/pull/716)) ([DPE-5581](https://warthogs.atlassian.net/browse/DPE-5581))
* Added pgAudit plugin/extension  ([PR #688](https://github.com/canonical/postgresql-k8s-operator/pull/688)) ([DPE-5116](https://warthogs.atlassian.net/browse/DPE-5116))
* Observability stack (COS) improvements
  *  Polished built-in Grafana dashboard ([PR #733](https://github.com/canonical/postgresql-k8s-operator/pull/733)) ([DPE-4469](https://warthogs.atlassian.net/browse/DPE-4469))
  *  Improved COS alert rule descriptions ([PR #727](https://github.com/canonical/postgresql-k8s-operator/pull/727)) ([DPE-5658](https://warthogs.atlassian.net/browse/DPE-5658))
* Added fully-featured terraform module ([PR #737](https://github.com/canonical/postgresql-k8s-operator/pull/737)) ([DPE-5627](https://warthogs.atlassian.net/browse/DPE-5627))
* S3 backups improvements ([PR #750](https://github.com/canonical/postgresql-k8s-operator/pull/750))

## Features and improvements
* Removed patching of private ops class. ([PR #692](https://github.com/canonical/postgresql-k8s-operator/pull/692))
* Switched charm libs from `tempo_k8s` to `tempo_coordinator_k8s` and test relay support of tracing traffic through `grafana-agent-k8s` ([PR #725](https://github.com/canonical/postgresql-k8s-operator/pull/725))
* Added check for low storage space on pgdata volume ([PR #685](https://github.com/canonical/postgresql-k8s-operator/pull/685)) ([DPE-5301](https://warthogs.atlassian.net/browse/DPE-5301))
* Re-enabled log forwarding ([PR #671](https://github.com/canonical/postgresql-k8s-operator/pull/671))
* Avoid replication slot deletion ([PR #680](https://github.com/canonical/postgresql-k8s-operator/pull/680)) ([DPE-3887](https://warthogs.atlassian.net/browse/DPE-3887))
* Added pgBackRest logrotate configuration ([PR #722](https://github.com/canonical/postgresql-k8s-operator/pull/722)) ([DPE-5600](https://warthogs.atlassian.net/browse/DPE-5600))
* Grant priviledges to non-public schemas ([PR #742](https://github.com/canonical/postgresql-k8s-operator/pull/742)) ([DPE-5387](https://warthogs.atlassian.net/browse/DPE-5387))
* Added TLS flag + CA to relation databag ([PR #719](https://github.com/canonical/postgresql-k8s-operator/pull/719)) ([DPE-5484](https://warthogs.atlassian.net/browse/DPE-5484))
* Added warning logs to Patroni reinitialisation ([PR #753](https://github.com/canonical/postgresql-k8s-operator/pull/753)) ([DPE-5712](https://warthogs.atlassian.net/browse/DPE-5712))
* Reduced pgdate permissions ([PR #759](https://github.com/canonical/postgresql-k8s-operator/pull/759)) ([DPE-5915](https://warthogs.atlassian.net/browse/DPE-5915))
* Split off new interface client app tests ([PR #761](https://github.com/canonical/postgresql-k8s-operator/pull/761))
* Temporarily disable log forwarding ([PR #757](https://github.com/canonical/postgresql-k8s-operator/pull/757))
* Changed owner of functions, procedures and aggregates ([PR #773](https://github.com/canonical/postgresql-k8s-operator/pull/773))
* Only update tls flags on leader ([PR #770](https://github.com/canonical/postgresql-k8s-operator/pull/770))
* Preload shared libs on normal PG start ([PR #774](https://github.com/canonical/postgresql-k8s-operator/pull/774)) ([DPE-6033](https://warthogs.atlassian.net/browse/DPE-6033))

## Bugfixes and maintenance
* Fixed PITR backup test instabilities ([PR #690](https://github.com/canonical/postgresql-k8s-operator/pull/690))
* Fixed some `postgresql.conf` parameters for hardening ([PR #702](https://github.com/canonical/postgresql-k8s-operator/pull/702)) ([DPE-5511](https://warthogs.atlassian.net/browse/DPE-5511))
* Fixed event deferring issue with missing S3 relation ([PR #762](https://github.com/canonical/postgresql-k8s-operator/pull/762)) ([DPE-5934](https://warthogs.atlassian.net/browse/DPE-5934))
* Fixed connection rejection rule in `pg_hba.conf` ([PR #751](https://github.com/canonical/postgresql-k8s-operator/pull/751)) ([DPE-5689](https://warthogs.atlassian.net/browse/DPE-5689))

[details=Libraries, testing, and CI]
* [Hotfix] Remove failing tests from CI ([PR #693](https://github.com/canonical/postgresql-k8s-operator/pull/693))
* Reenable full cluster restart tests ([PR #559](https://github.com/canonical/postgresql-k8s-operator/pull/559)) ([DPE-5327](https://warthogs.atlassian.net/browse/DPE-5327))
* Reenable label rollback test ([PR #754](https://github.com/canonical/postgresql-k8s-operator/pull/754)) ([DPE-5693](https://warthogs.atlassian.net/browse/DPE-5693))
* Use more meaningful group naming for multi-group tests ([PR #707](https://github.com/canonical/postgresql-k8s-operator/pull/707))
* Reenable labelling tests ([PR #728](https://github.com/canonical/postgresql-k8s-operator/pull/728))
* increase async replication tests coverage ([PR #748](https://github.com/canonical/postgresql-k8s-operator/pull/748)) ([DPE-5662](https://warthogs.atlassian.net/browse/DPE-5662))
* Run integration tests against Juju 3.6 ([PR #689](https://github.com/canonical/postgresql-k8s-operator/pull/689)) ([DPE-4977](https://warthogs.atlassian.net/browse/DPE-4977))
* Lock file maintenance Python dependencies ([PR #777](https://github.com/canonical/postgresql-k8s-operator/pull/777))
* Migrate config .github/renovate.json5 ([PR #769](https://github.com/canonical/postgresql-k8s-operator/pull/769))
* Switch from tox build wrapper to charmcraft.yaml overrides ([PR #708](https://github.com/canonical/postgresql-k8s-operator/pull/708))
* Update codecov/codecov-action action to v5 ([PR #771](https://github.com/canonical/postgresql-k8s-operator/pull/771))
* Update data-platform-workflows to v23.0.5 ([PR #776](https://github.com/canonical/postgresql-k8s-operator/pull/776))
* Update dependency juju/juju to v2.9.51 ([PR #717](https://github.com/canonical/postgresql-k8s-operator/pull/717))
* Update dependency juju/juju to v3.4.6 ([PR #720](https://github.com/canonical/postgresql-k8s-operator/pull/720))
* Update dependency ubuntu to v24 ([PR #711](https://github.com/canonical/postgresql-k8s-operator/pull/711))
* Update ghcr.io/canonical/charmed-postgresql Docker tag to v14.13 ([PR #658](https://github.com/canonical/postgresql-k8s-operator/pull/658))
[/details]

## Known limitations

 * [Juju 3.6.1+](https://discourse.charmhub.io/t/roadmap-releases/5064#juju-juju-361-11-dec-2024-2) is required for [Terraform-Provider-Juju](https://github.com/juju/terraform-provider-juju) consistent usage ([more details here](https://github.com/juju/terraform-provider-juju/issues/608)).

## Requirements and compatibility
* (no change) Minimum Juju 2 version: `v.2.9.49`
* (no change) Minimum Juju 3 version: `v.3.4.3`
* (increased) Recommended Juju 3 version: `v.3.6.1`

See the [system requirements] for more details about Juju versions and other software and hardware prerequisites.

### Integration tests
Below are some of the charm integrations tested with this revision on different Juju environments and architectures:
* Juju `v.2.9.51` on `amd64`
* Juju  `v.3.4.6` on `amd64` and `arm64`
* Juju  `v.3.6.1` on `amd64` and `arm64`

|  Software | Revision | Tested on | |
|-----|-----|----|---|
| [postgresql-test-app] | `rev 279` | ![juju-2_amd64] ![juju-3_amd64] |
|   | `rev 278` | ![juju-3_arm64] |
| [data-integrator] | `rev 41` | ![juju-2_amd64] ![juju-3_amd64] |
|   | `rev 40` | ![juju-3_arm64] |
| [s3-integrator] | `rev 77` |  ![juju-2_amd64] ![juju-3_amd64]  |
|   | `rev 78` | ![juju-3_arm64]  |
| [tls-certificates-operator] | `rev 22` | ![juju-2_amd64] |
| [self-signed-certificates] | `rev 155` |  ![juju-3_amd64]  |
|  | `rev 205` | ![juju-3_arm64] |
| [mattermost-k8s] | `rev 27` |  ![juju-2_amd64] ![juju-3_amd64] |
| [indico] | `rev 233` |  ![juju-2_amd64] ![juju-3_amd64] |
| [redis-k8s] | `rev 7`|  ![juju-2_amd64] ![juju-3_amd64] |
| | `rev 38` |  ![juju-2_amd64] ![juju-3_amd64] |
| [discourse-k8s] | `rev 173` | ![juju-2_amd64] ![juju-3_amd64] |

### Packaging
This charm is based on the Charmed PostgreSQL K8s ROCK revision `164`. It packages:
* [postgresql] `v.14.13`
* [pgbouncer] `v.1.21`
* [patroni] `v.3.1.2`
* [pgBackRest] `v.2.53`
* [prometheus-postgres-exporter] `v.0.12.1`

<!-- DISCOURSE TOPICS-->
[All revisions]: /t/11872
[system requirements]: /t/11744
[How to perform a minor upgrade]: /t/12095

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
[landscape-client]: https://charmhub.io/landscape-client
[ubuntu-advantage]: https://charmhub.io/ubuntu-advantage
[mattermost-k8s]: https://charmhub.io/mattermost-k8s
[redis-k8s]: https://charmhub.io/redis-k8s

[`/lib/charms` directory on GitHub]: https://github.com/canonical/postgresql-k8s-operator/tree/rev463/lib/charms
[`metadata.yaml` file on GitHub]: https://github.com/canonical/postgresql-k8s-operator/blob/rev463/metadata.yaml

[postgresql]: https://launchpad.net/ubuntu/+source/postgresql-14/
[pgbouncer]: https://launchpad.net/~data-platform/+archive/ubuntu/pgbouncer
[patroni]: https://launchpad.net/~data-platform/+archive/ubuntu/patroni
[pgBackRest]: https://launchpad.net/~data-platform/+archive/ubuntu/pgbackrest
[prometheus-postgres-exporter]: https://launchpad.net/~data-platform/+archive/ubuntu/postgres-exporter

[juju-2_amd64]: https://img.shields.io/badge/Juju_2.9.51-amd64-darkgreen?labelColor=ea7d56 
[juju-3_amd64]: https://img.shields.io/badge/Juju_3.4.6-amd64-darkgreen?labelColor=E95420 
[juju-3_arm64]: https://img.shields.io/badge/Juju_3.4.6-arm64-blue?labelColor=E95420