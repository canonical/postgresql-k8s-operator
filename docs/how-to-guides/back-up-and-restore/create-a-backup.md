


```{note}
**Note**: All commands are written for `juju >= v.3.0`

If you are using an earlier version, check the [Juju 3.0 Release Notes](https://juju.is/docs/juju/roadmap#juju-3-0-0---22-oct-2022).
```

# How to create and list backups

This guide contains recommended steps and useful commands for creating and managing backups to ensure smooth restores.

## Prerequisites

* A cluster with [at least three-nodes](/) deployed
* Access to S3 storage
* [Configured settings for S3 storage](/)

---

## Create a backup
Once you have a three-node cluster with configurations set for S3 storage, check that Charmed PostgreSQL is `active` and `idle` with `juju status`. 

Once Charmed PostgreSQL is `active` and `idle`, you can create your first backup with the `create-backup` command:
```shell
juju run postgresql-k8s/leader create-backup
```

By default, backups created with command above will be **full** backups: a copy of *all* your data will be stored in S3. There are 2 other supported types of backups (available in revision 263+):
* Differential: Only modified files since the last full backup will be stored.
* Incremental: Only modified files since the last successful backup (of any type) will be stored.

To specify the desired backup type, use the [`type`](https://charmhub.io/postgresql-k8s/actions#create-backup) parameter:
```shell
juju run postgresql-k8s/leader create-backup type={full|differential|incremental}
```

**Tip**: To avoid unnecessary service downtime, always use non-primary units for the action `create-backup`. Keep in mind that:
* TLS enabled:  disables the command from running on *primary units*.
* TLS **not** enabled: disables the command from running on *non-primary units*.

## List backups
You can list your available, failed, and in-progress backups by running the `list-backups` command:
```shell
juju run postgresql-k8s/leader list-backups
```

