# Release Notes

This page provides high-level overviews of the dependencies and features that are supported by each revision in every stable release.

To learn more about the different release tracks and channels, see the [Juju documentation about channels](https://juju.is/docs/juju/channel#heading--risk).

To see all releases and commits, check the [Charmed PostgreSQL Releases page on GitHub](https://github.com/canonical/postgresql-k8s-operator/releases).

## Dependencies and supported features

For a given release, this table shows:
* The PostgreSQL version packaged inside
* The minimum Juju version required to reliably operate **all** features of the release
   > This charm still supports older versions of Juju down to 2.9. See the [Juju section of the system requirements](/t/) for more details
* Support for specific features

| Revision | PostgreSQL version | Juju version | [TLS encryption](/t/9685)* | [COS monitoring](/t/10600) | [Minor version upgrades](/t/) | [Cross-regional async replication](/t/) | [Point-in-time recovery](/t/) | [PITR Timelines](/t/) |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| [462], [463] | 14.13 | `3.6.1+` | ![check] | ![check] | ![check] | ![check] | ![check] | ![check] |
| [444], [445] | 14.12 | `3.4.3+` | ![check] | ![check] | ![check] | ![check] | ![check] | |
| [381], [382] | 14.12 | `3.4.3+` | ![check] | ![check] | ![check] | ![check] | ![check] | |
| [280], [281] | 14.11 | `3.4.2+` | ![check] | ![check] | ![check] | ![check] | |
| [193] | 14.10 | `3.4.2+` | ![check] | ![check] | ![check] | ![check] | |
| [177] | 14.9 | `3.1.6+` |  | ![check] | ![check] | |
| [158] | 14.9 | `3.1.5+` |  | ![check] | ![check] | |
| [73]  | 14.7 | `2.9.32+` |  |  | |

**TLS encryption***: Support for **`v2` or higher** of the [`tls-certificates` interface](https://charmhub.io/tls-certificates-interface/libraries/tls_certificates). This means that you can integrate with [modern TLS charms](https://charmhub.io/topics/security-with-x-509-certificates).

For more details about a particular revision, refer to its dedicated Release Notes page.
For more details about each feature/interface, refer to their dedicated How-To guide.

## Architecture and base
Several [revisions](https://juju.is/docs/sdk/revision) are released simultaneously for different [bases/series](https://juju.is/docs/juju/base) using the same charm code. In other words, one release contains multiple revisions.

> If you do not specify a revision on deploy time, Juju will automatically choose the revision that matches your base and architecture.

> If you deploy a specific revision, **you must make sure it matches your base and architecture** via the tables below or with [`juju info`](https://juju.is/docs/juju/juju-info).

### Release 462-463

| Revision | amd64 | arm64 | Ubuntu 22.04 LTS
|:--------:|:-----:|:-----:|:-----:|
|[462]  | ![check] | |  ![check]  |
|[463] | | ![check] |  ![check]  |

[details=Older releases]
### Release 444-445

| Revision | amd64 | arm64 | Ubuntu 22.04 LTS
|:--------:|:-----:|:-----:|:-----:|
|[445] | | ![check] | ![check]  |
|[444] | ![check] | | ![check] |

### Release 381-382

| Revision | amd64 | arm64 | Ubuntu 22.04 LTS
|:--------:|:-----:|:-----:|:-----:|
|[382] | | ![check] | ![check]  |
|[381] | ![check] | | ![check] |

### Release 280-281

| Revision| amd64 | arm64 | Ubuntu 22.04 LTS
|:--------:|:-----:|:-----:|:-----:|
|[281] |![check]| | ![check]   |
|[280] |  | ![check]| ![check] |

### Release 193

| Revision| amd64 | arm64 | Ubuntu 22.04 LTS
|:--------:|:-----:|:-----:|:-----:|
|[193] | ![check]| | ![check]  |

### Release 177

| Revision| amd64 | arm64 | Ubuntu 22.04 LTS
|:--------:|:-----:|:-----:|:-----:|
|[177] |![check]| | ![check]   |

### Release 158

| Revision| amd64 | arm64 | Ubuntu 22.04 LTS
|:--------:|:-----:|:-----:|:-----:|
|[158] |![check]| | ![check]   |

### Release 73

| Revision| amd64 | arm64 | Ubuntu 22.04 LTS
|:--------:|:-----:|:-----:|:-----:|
|[73] |![check]| | ![check]   |

[/details]

## Plugins/extensions

For a list of all plugins supported for each revision, see the reference page [Plugins/extensions](/t/10945).

[note]
 Our release notes are an ongoing work in progress. If there is any additional information about releases that you would like to see or suggestions for other improvements, don't hesitate to contact us on [Matrix ](https://matrix.to/#/#charmhub-data-platform:ubuntu.com) or [leave a comment](https://discourse.charmhub.io/t/charmed-postgresql-k8s-reference-release-notes/11872).
[/note]

<!-- LINKS -->
[462]: /t/16008
[463]: /t/16008
[445]: /t/15966
[444]: /t/15966
[382]: /t/15442
[381]: /t/15442
[281]: /t/14068
[280]: /t/14068
[193]: /t/13208
[177]: /t/12668
[158]: /t/11874
[73]: /t/11873

<!--BADGES-->
[check]: https://img.icons8.com/color/20/checkmark--v1.png