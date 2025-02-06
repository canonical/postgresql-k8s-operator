[note]
**Note**: All commands are written for `juju >= v.3.0`

If you are using an earlier version, check the [Juju 3.0 Release Notes](https://juju.is/docs/juju/roadmap#heading--juju-3-0-0---22-oct-2022).
[/note]

# How to restore a local backup 

This is a guide on how to restore a locally made backup.

To restore a backup that was made from a *different* cluster, (i.e. cluster migration via restore), see [How to migrate cluster using backups](/t/charmed-postgresql-k8s-how-to-migrate-clusters/9598?channel=14/stable).

## Prerequisites
- Deployments have been [scaled-down](/t/charmed-postgresql-k8s-how-to-manage-units/9592?channel=14/stable) to a single PostgreSQL unit (scale it up after the backup is restored)
- Access to S3 storage
- [Configured settings for S3 storage](/t/charmed-postgresql-k8s-how-to-configure-s3/9595?channel=14/stable)
- [Existing backups in your S3-storage](/t/charmed-postgresql-k8s-how-to-create-and-list-backups/9596?channel=14/stable)
- [Point-in-time recovery](#point-in-time-recovery) requires the following PostgreSQL K8s charm revisions:
   - 382+ for `arm64`
  -  381+ for `amd64`

## Summary
* [List backups](#list-backups)
* [Point-in-time recovery](#point-in-time-recovery)
* [Restore backup](#restore-backup)

---

## List backups
To view the available backups to restore, use the command `list-backups`:
```shell
juju run postgresql-k8s/leader list-backups
```

This should show your available backups like in the sample output below:
```shell
list-backups: |-
  Storage bucket name: canonical-postgres
  Backups base path: /test/backup/

  backup-id            | action             | ... | timeline
  ---------------------------------------------------------------------------
  2024-07-22T13:11:56Z | full backup        | ... | 1
  2024-07-22T14:12:45Z | incremental backup | ... | 1
  2024-07-22T15:34:24Z | restore            | ... | 2
  2024-07-22T16:26:48Z | incremental backup | ... | 2
  2024-07-22T17:17:59Z | full               | ... | 2
  2024-07-22T18:05:32Z | restore            | ... | 3
```

Below is a complete list of parameters shown for each backup/restore operation:
* `backup-id`: unique identifier of the backup.
* `action`: indicates the action performed by the user through one of the charm action; can be any of full backup, incremental backup, differential backup or restore.
* `status`: either finished (successfully) or failed.
* `reference-backup-id` 
* `LSN start/stop`: a database specific number (or timestamp) to identify its state.
* `start-time`: records start of the backup operation.
* `finish-time`: records end of the backup operation.
* `backup-path`: path of the backup related files in the S3 repository.
* `timeline`: number which identifies different branches in the database transactions history; every time a restore or PITR is made, this number is incremented by 1.

## Point-in-time recovery
Point-in-time recovery (PITR) is a PostgreSQL feature that enables restorations to the database state at specific points in time.

After performing a PITR in a PostgreSQL cluster, a new timeline is created to track from the point to where the database was restored. They can be tracked via the `timeline` parameter in the `list-backups` output.

## Restore backup
To restore a backup from that list, run the `restore` command and pass the parameter corresponding to the backup type.

When the user needs to restore a specific backup that was made, they can use the `backup-id` that is listed in the `list-backups` output. 
 ```shell
juju run postgresql-k8s/leader restore backup-id=YYYY-MM-DDTHH:MM:SSZ
```
However, if the user needs to restore to a specific point in time between different backups (e.g. to restore only specific transactions made between those backups), they can use the `restore-to-time` parameter to pass a timestamp related to the moment they want to restore.
 ```shell
juju run postgresql-k8s/leader restore restore-to-time="YYYY-MM-DDTHH:MM:SSZ"
```

Your restore will then be in progress.

It’s also possible to restore to the latest point from a specific timeline by passing the ID of a backup taken on that timeline and `restore-to-time=latest` when requesting a restore:

 ```shell
juju run postgresql-k8s/leader restore restore-to-time=latest
```