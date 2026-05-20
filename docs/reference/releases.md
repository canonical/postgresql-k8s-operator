(releases)=
# Releases

This page provides high-level overviews of the dependencies and features that are supported by each revision in every stable release.

To learn more about the different release tracks and channels, see the [Juju documentation about channels](https://documentation.ubuntu.com/juju/3.6/reference/charm/#risk).

To see all releases and commits, check the [Charmed PostgreSQL Releases on GitHub](https://github.com/canonical/postgresql-k8s-operator/releases).

## Dependencies and supported features

For a given release, this table shows:
* The PostgreSQL 16 version packaged inside
* The minimum Juju version required to reliably operate **all** features of the release
* Support for specific features

| Revisions | PostgreSQL version | Juju version | [TLS encryption](/how-to/enable-tls) | [COS monitoring](/how-to/monitoring-cos/index) | [Minor version upgrades](/how-to/upgrade/index) | [Cross-regional async replication](/how-to/cross-regional-async-replication/index) | [Point-in-time recovery](/how-to/back-up-and-restore/restore-a-backup) | [PITR Timelines](/how-to/back-up-and-restore/restore-a-backup) |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| [901], [902] | 16.13 | `3.6.21+` | ![check] | ![check] | ![check] | ![check] | ![check] | ![check] |

## Architecture and base

Several [revisions](https://documentation.ubuntu.com/juju/3.6/reference/charm/#charm-revision) are released simultaneously for different architectures using the same charm code.

If you do not specify a revision on deploy time, Juju will automatically choose the revision that matches your base and architecture.

```{caution}
If you deploy with the `--revision` flag, **you must make sure the revision matches your base and architecture**. 

Check the table in release notes, or use [`juju info`](https://juju.is/docs/juju/juju-info).
```

## Plugins/extensions

For a list of all plugins supported for each revision, see the reference page [Plugins/extensions](/reference/plugins-extensions).


> **Note** Our release notes are an ongoing work in progress. If there is any additional information about releases that you would like to see or suggestions for other improvements, don't hesitate to contact us on [Matrix ](https://matrix.to/#/#charmhub-data-platform:ubuntu.com) or [leave a comment](https://discourse.charmhub.io/t/charmed-postgresql-k8s-reference-release-notes/11872).


<!--BADGES-->
[check]: https://img.icons8.com/color/20/checkmark--v1.png

<!-- LINKS -->
[901]: https://github.com/canonical/postgresql-k8s-operator/releases/tag/v16%2F1.111.0
[902]: https://github.com/canonical/postgresql-k8s-operator/releases/tag/v16%2F1.111.0
