# Releases

This page provides high-level overviews of the dependencies and features that are supported by each revision in every stable release.

To learn more about the different release tracks and channels, see the [Juju documentation about channels](https://documentation.ubuntu.com/juju/3.6/reference/charm/#risk).

To see all releases and commits, check the [Charmed PostgreSQL Releases on GitHub](https://github.com/canonical/postgresql-k8s-operator/releases).

## Dependencies and supported features

For a given release, this table shows:
* The PostgreSQL 14 version packaged inside
* The minimum Juju version required to reliably operate **all** features of the release
   > This charm still supports older versions of Juju down to 2.9. See the [system requirements](/reference/system-requirements) for more details
* Support for specific features

| Revision | PostgreSQL version | Juju version | [TLS encryption](/how-to/enable-tls)* | [COS monitoring](/how-to/monitoring-cos/index) | [Minor version upgrades](/how-to/upgrade/index) | [Cross-regional async replication](/how-to/cross-regional-async-replication/index) | [Point-in-time recovery](/how-to/back-up-and-restore/restore-a-backup) | [PITR Timelines](/how-to/back-up-and-restore/restore-a-backup) |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| [717], [718] | 14.20 | `3.6.1+` | ![check] | ![check] | ![check] | ![check] | ![check] | ![check] |
| [494], [495] | 14.15 | `3.6.1+` | ![check] | ![check] | ![check] | ![check] | ![check] | ![check] |
| [462], [463] | 14.13 | `3.6.1+` | ![check] | ![check] | ![check] | ![check] | ![check] | ![check] |
| [444], [445] | 14.12 | `3.4.3+` | ![check] | ![check] | ![check] | ![check] | ![check] | |
| [381], [382] | 14.12 | `3.4.3+` | ![check] | ![check] | ![check] | ![check] | ![check] | |
| [280], [281] | 14.11 | `3.4.2+` | ![check] | ![check] | ![check] | ![check] | |
| [193] | 14.10 | `3.4.2+` | ![check] | ![check] | ![check] | ![check] | |
| [177] | 14.9 | `3.1.6+` |  | ![check] | ![check] | |
| [158] | 14.9 | `3.1.5+` |  | ![check] | ![check] | |
| [73]  | 14.7 | `2.9.32+` |  |  | |

\* **TLS encryption***: Support for **`v2` or higher** of the [`tls-certificates` interface](https://charmhub.io/tls-certificates-interface/libraries/tls_certificates). This means that you can integrate with [modern TLS charms](https://charmhub.io/topics/security-with-x-509-certificates).

## Architecture and base

Several [revisions](https://documentation.ubuntu.com/juju/3.6/reference/charm/#charm-revision) are released simultaneously for different bases using the same charm code. In other words, one release contains multiple revisions.

If you do not specify a revision on deploy time, Juju will automatically choose the revision that matches your base and architecture.

```{caution}
If you deploy with the `--revision` flag, **you must make sure the revision matches your base and architecture**. 

Check the tables below, or use [`juju info`](https://juju.is/docs/juju/juju-info).
```

### Release 717, 718

| Revision | amd64 | arm64 | Ubuntu 22.04 LTS
|:--------:|:-----:|:-----:|:-----:|
|[718]    |        | ![check] |  ![check]  |
|[717]    | ![check] | |  ![check]  |

<details>
<summary>Older releases</summary>

| Revision | amd64 | arm64 | Ubuntu 22.04 LTS
|:--------:|:-----:|:-----:|:-----:|
|[494] |         | ![check] |  ![check]  |
|[495] | ![check] |         |  ![check]  |
|[462] |![check] |          | ![check] |
|[463] |         | ![check] | ![check] |
|[445] |         | ![check] | ![check] |
|[444] |![check] |          | ![check] |
|[382] |         | ![check] | ![check] |
|[381] |![check] |          | ![check] |
|[281] |![check] |          | ![check] |
|[280] |         |![check]  | ![check] |
|[193] |![check] |          | ![check] |
|[177] |![check] |          | ![check] |
|[158] |![check] |          | ![check] |
|[73]  |![check] |          | ![check] |

</details>

## Plugins/extensions

For a list of all plugins supported for each revision, see the reference page [Plugins/extensions](/reference/plugins-extensions).


> **Note** Our release notes are an ongoing work in progress. If there is any additional information about releases that you would like to see or suggestions for other improvements, don't hesitate to contact us on [Matrix ](https://matrix.to/#/#charmhub-data-platform:ubuntu.com) or [leave a comment](https://discourse.charmhub.io/t/charmed-postgresql-k8s-reference-release-notes/11872).

<!-- LINKS -->
[717]:
[718]:

[494]: https://github.com/canonical/postgresql-k8s-operator/releases/tag/rev494
[495]: https://github.com/canonical/postgresql-k8s-operator/releases/tag/rev494

[462]: https://github.com/canonical/postgresql-k8s-operator/releases/tag/rev462
[463]: https://github.com/canonical/postgresql-k8s-operator/releases/tag/rev462

[445]: https://github.com/canonical/postgresql-k8s-operator/releases/tag/rev444
[444]: https://github.com/canonical/postgresql-k8s-operator/releases/tag/rev444

[382]: https://github.com/canonical/postgresql-k8s-operator/releases/tag/rev381
[381]: https://github.com/canonical/postgresql-k8s-operator/releases/tag/rev381

[281]: https://github.com/canonical/postgresql-k8s-operator/releases/tag/rev280
[280]: https://github.com/canonical/postgresql-k8s-operator/releases/tag/rev280

[193]: https://github.com/canonical/postgresql-k8s-operator/releases/tag/rev193
[177]: https://github.com/canonical/postgresql-k8s-operator/releases/tag/rev177
[158]: https://github.com/canonical/postgresql-k8s-operator/releases/tag/rev158
[73]: https://github.com/canonical/postgresql-k8s-operator/releases/tag/rev73

<!--BADGES-->
[check]: https://img.icons8.com/color/20/checkmark--v1.png

