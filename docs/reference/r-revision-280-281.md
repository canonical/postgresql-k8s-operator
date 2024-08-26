>Reference > Release Notes > [All revisions](/t/11872) > Revision 280/281
# Revision 280/281

<sub>June 28, 2024</sub>

Dear community,

Canonical's newest Charmed PostgreSQL K8s operator has been published in the '14/stable' [channel](https://charmhub.io/postgresql-k8s?channel=14/stable) :tada:

Due to the newly added support for `arm64` architecture, the PostgreSQL charm now releases two revisions simultaneously: 
* Revision 281 is built for `amd64`
* Revision 280 is built for for `arm64`

To make sure you deploy for the right architecture, we recommend setting an [architecture constraint](https://juju.is/docs/juju/constraint#heading--arch) for your entire juju model.

Otherwise, it can be done at deploy time with the `--constraints` flag:
```shell
juju deploy postgresql-k8s --constraints arch=<arch> --trust
```
where `<arch>` can be `amd64` or `arm64`.

[note]
If you are jumping over several stable revisions, make sure to check [previous release notes](/t/11872) before upgrading to this revision.
[/note]

## Highlights

* Upgraded PostgreSQL from v.14.10 → v.14.11 ([PR #432](https://github.com/canonical/postgresql-operator/pull/432))
  * Check the official [PostgreSQL release notes](https://www.postgresql.org/docs/release/14.11/)
* Added support for ARM64 architecture ([PR #408](https://github.com/canonical/postgresql-k8s-operator/pull/408))
* Added support for cross-regional asynchronous replication ([PR #447](https://github.com/canonical/postgresql-k8s-operator/pull/447)) ([DPE-2897](https://warthogs.atlassian.net/browse/DPE-2897))
  * This feature focuses on disaster recovery by distributing data across different servers. Check our [new how-to guides](https://charmhub.io/postgresql-k8s/docs/h-async-setup) for a walkthrough of the cross-model setup, promotion, switchover, and other details.
* Added support for tracing with Tempo K8s ([PR #497](https://github.com/canonical/postgresql-k8s-operator/pull/497))
  * Check the new guide: [How to enable tracing](https://charmhub.io/postgresql-k8s/docs/h-enable-tracing)
* Released new [Charmed Sysbench operator](https://charmhub.io/sysbench) for easy performance testing

### Enhancements
* Added timescaledb plugin/extension ([PR #488](https://github.com/canonical/postgresql-k8s-operator/pull/488))
   * See the [Configuration tab]((https://charmhub.io/postgresql-k8s/configuration#plugin_timescaledb_enable)) for all parameters.
* Added incremental and differential backup support ([PR #487](https://github.com/canonical/postgresql-k8s-operator/pull/487))([PR #476](https://github.com/canonical/postgresql-k8s-operator/pull/476))([DPE-4464](https://warthogs.atlassian.net/browse/DPE-4464))
  * Check the guide: [How to create and list backups](https://charmhub.io/postgresql-k8s/docs/h-create-backup)
* Added support for disabling the operator ([DPE-2470](https://warthogs.atlassian.net/browse/DPE-2470))
* Added configuration option for backup retention time  ([PR #477](https://github.com/canonical/postgresql-k8s-operator/pull/477))([DPE-4401](https://warthogs.atlassian.net/browse/DPE-4401))
  * See the[ Configuration tab](https://charmhub.io/s3-integrator/configuration?channel=latest/edge#experimental-delete-older-than-days) for all parameters
* Added message to inform users about missing `--trust` flag ([PR #440](https://github.com/canonical/postgresql-k8s-operator/pull/440))([DPE-3885](https://warthogs.atlassian.net/browse/DPE-3885))
* Added `experimental_max_connections` config option ([PR #500](https://github.com/canonical/postgresql-k8s-operator/pull/500))
* Introduced a block on legacy roles request (modern interface only) ([PR#391](https://github.com/canonical/postgresql-k8s-operator/pull/391))([DPE-3099](https://warthogs.atlassian.net/browse/DPE-3099))

### Bugfixes

* Fixed large objects ownership ([PR #390](https://github.com/canonical/postgresql-k8s-operator/pull/390))([DPE-3551](https://warthogs.atlassian.net/browse/DPE-3551))
* Fixed shared buffers validation ([PR #396](https://github.com/canonical/postgresql-k8s-operator/pull/396))([DPE-3594](https://warthogs.atlassian.net/browse/DPE-3594))
* Fixed handling S3 relation in primary non-leader unit ([PR #375](https://github.com/canonical/postgresql-k8s-operator/pull/375))([DPE-3349](https://warthogs.atlassian.net/browse/DPE-3349))
* Stabilized SST and network cut tests ([PR #385](https://github.com/canonical/postgresql-k8s-operator/pull/385))([DPE-3473](https://warthogs.atlassian.net/browse/DPE-3473))
* Fixed pod reconciliation: rerender config/service on pod recreation ([PR#461](https://github.com/canonical/postgresql-k8s-operator/pull/461))([DPE-2671](https://warthogs.atlassian.net/browse/DPE-2671))
* Addressed main instability sources on backups integration tests ([PR#496](https://github.com/canonical/postgresql-k8s-operator/pull/496))([DPE-4427](https://warthogs.atlassian.net/browse/DPE-4427))
* Fixed scale up with S3 and TLS relations in ([PR#489](https://github.com/canonical/postgresql-k8s-operator/pull/489))([DPE-4456](https://warthogs.atlassian.net/browse/DPE-4456))

Canonical Data issues are now public on both [Jira](https://warthogs.atlassian.net/jira/software/c/projects/DPE/issues/) and [GitHub](https://github.com/canonical/postgresql-k8s-operator/issues).

For a full list of all changes in this revision, see the [GitHub Release](https://github.com/canonical/postgresql-k8s-operator/releases/tag/rev281). 

## Technical details
This section contains some technical details about the charm's contents and dependencies.  Make sure to also check the [system requirements](/t/11744).

### Packaging
This charm is based on the [`charmed-postgresql` snap](https://snapcraft.io/charmed-postgresql) (pinned revision 113). It packages:
* postgresql `v.14.11`
	* [`14.11-0ubuntu0.22.04.1`](https://launchpad.net/ubuntu/+source/postgresql-14/14.11-0ubuntu0.22.04.1) 
* pgbouncer `v.1.21`
	* [`1.21.0-0ubuntu0.22.04.1~ppa1`](https://launchpad.net/~data-platform/+archive/ubuntu/pgbouncer)
* patroni `v.3.1.2 `
	* [`3.1.2-0ubuntu0.22.04.1~ppa2`](https://launchpad.net/~data-platform/+archive/ubuntu/patroni)
* pgBackRest `v.2.48`
	* [`2.48-0ubuntu0.22.04.1~ppa1`](https://launchpad.net/~data-platform/+archive/ubuntu/pgbackrest)
* prometheus-postgres-exporter `v.0.12.1`

### Libraries and interfaces
This charm revision imports the following libraries: 

* **grafana_agent `v0`** for integration with Grafana 
    * Implements  `cos_agent` interface
* **rolling_ops `v0`** for rolling operations across units 
    * Implements `rolling_op` interface
* **tempo_k8s `v1`, `v2`** for integration with Tempo charm
    * Implements `tracing` interface
* **tls_certificates_interface `v2`** for integration with TLS charms
    * Implements `tls-certificates` interface

See the [`/lib/charms` directory on GitHub](https://github.com/canonical/postgresql-k8s-operator/tree/main/lib/charms) for more details about all supported libraries.

See the [`metadata.yaml` file on GitHub](https://github.com/canonical/postgresql-k8s-operator/blob/main/metadata.yaml#L20-L77) for a full list of supported interfaces

## Contact us

Charmed PostgreSQL K8s is an open source project that warmly welcomes community contributions, suggestions, fixes, and constructive feedback.  
* Raise software issues or feature requests on [**GitHub**](https://github.com/canonical/postgresql-k8s-operator/issues)  
*  Report security issues through [**Launchpad**](https://wiki.ubuntu.com/DebuggingSecurity#How%20to%20File)  
* Contact the Canonical Data Platform team through our [Matrix](https://matrix.to/#/#charmhub-data-platform:ubuntu.com) channel.