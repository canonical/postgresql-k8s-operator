# Releases

This page provides high-level overviews of the dependencies and features that are supported by each revision in every stable release.

To learn more about the different release tracks and channels, see the [Juju documentation about channels](https://juju.is/docs/juju/channel#risk).

To see all releases and commits, check the [Charmed PostgreSQL Releases on GitHub](https://github.com/canonical/postgresql-k8s-operator/releases).

## Dependencies and supported features

For a given release, this table shows:
* The PostgreSQL 14 version packaged inside
* The minimum Juju version required to reliably operate **all** features of the release
   > This charm still supports older versions of Juju down to 2.9. See the [system requirements](/reference/system-requirements) for more details
* Support for specific features

| Revision | PostgreSQL version | Juju version | [TLS encryption](/how-to/enable-tls)* | [COS monitoring](/how-to/monitoring-cos/index) | [Minor version upgrades](/how-to/upgrade/index) | [Cross-regional async replication](/how-to/cross-regional-async-replication/index) | [Point-in-time recovery](/how-to/back-up-and-restore/restore-a-backup) | [PITR Timelines](/how-to/back-up-and-restore/restore-a-backup) |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
|   |   |   | ![check] | ![check] | ![check] | ![check] | ![check] |

\* **TLS encryption***: Support for **`v2` or higher** of the [`tls-certificates` interface](https://charmhub.io/tls-certificates-interface/libraries/tls_certificates). This means that you can integrate with [modern TLS charms](https://charmhub.io/topics/security-with-x-509-certificates).

## Architecture and base

Several [revisions](https://juju.is/docs/sdk/revision) are released simultaneously for different [bases/series](https://juju.is/docs/juju/base) using the same charm code. In other words, one release contains multiple revisions.

If you do not specify a revision on deploy time, Juju will automatically choose the revision that matches your base and architecture.

```{caution}
If you deploy with the `--revision` flag, **you must make sure the revision matches your base and architecture**. 

Check the tables below, or use [`juju info`](https://juju.is/docs/juju/juju-info).
```

## Plugins/extensions

For a list of all plugins supported for each revision, see the reference page [Plugins/extensions](/reference/plugins-extensions).


> **Note** Our release notes are an ongoing work in progress. If there is any additional information about releases that you would like to see or suggestions for other improvements, don't hesitate to contact us on [Matrix ](https://matrix.to/#/#charmhub-data-platform:ubuntu.com) or [leave a comment](https://discourse.charmhub.io/t/charmed-postgresql-k8s-reference-release-notes/11872).


<!--BADGES-->
[check]: https://img.icons8.com/color/20/checkmark--v1.png

