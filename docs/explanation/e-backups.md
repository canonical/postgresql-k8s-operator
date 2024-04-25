# Backup flowcharts
This document contains backups management flowchart, including all major hooks. This sources can be found at [src/backups.py](https://github.com/canonical/postgresql-k8s-operator/blob/main/src/backups.py).

## Hook Handler Flowcharts
These flowcharts detail the control flow of the hooks in this program. Unless otherwise stated, **a hook deferral is always followed by a return**.

## On S3 Credentials Changed Hook
[Click to navigate the mermaid diagram on GitHub](https://github.com/canonical/postgresql-k8s-operator/blob/main/docs/explanation/e-backups.md).

```mermaid
flowchart TD
  hook_fired([s3-credentials-changed Hook]) --> has_cluster_initialised{Has cluster\n initialised?}
  has_cluster_initialised -- no --> defer>defer]
  defer --> rtn([return])
  has_cluster_initialised -- yes --> are_all_required_settings_provided{Are all required\nS3 settings provided?}
  are_all_required_settings_provided -- no --> rtn
  are_all_required_settings_provided -- yes --> render_pgbackrest_config[Update backup settings]
  render_pgbackrest_config --> is_leader{Is current\nunit leader?}
  is_leader -- no --> rtn
  is_leader -- yes -->  is_blocked{Is unit in\nblocked state?}
  is_blocked -- yes --> rtn
  is_blocked -- no --> could_initialise_stanza{Could initialise\npgBackRest Stanza?}
  could_initialise_stanza -- no --> set_blocked[Set Blocked\nstatus]
  set_blocked --> rtn
  could_initialise_stanza -- yes --> is_wal_archiving_to_s3_working{Is WAL archiving\nto S3 working?}
  is_wal_archiving_to_s3_working -- no --> set_blocked
  is_wal_archiving_to_s3_working -- yes --> is_tls_disabled_or_single_unit_cluster{Is TLS disabled or\nsingle unit cluster}
  is_tls_disabled_or_single_unit_cluster -- yes --> stop_pgbackrest_tls_server[Stop pgBackRest\nTLS server]
  is_tls_disabled_or_single_unit_cluster -- no --> is_replica_and_tls_server_not_running_on_primary{Is current\nunit a replica\nand TLS server isn't\nrunning on primary?}
  is_replica_and_tls_server_not_running_on_primary -- yes --> rtn
  stop_pgbackrest_tls_server --> rtn
  is_replica_and_tls_server_not_running_on_primary -- no --> start_pgbackrest_tls_server[Start pgBackRest\nTLS server]
  start_pgbackrest_tls_server --> rtn
```

When certificates are received from TLS certificates operator through the `certificates` relation (or the relation is removed) the steps starting from `Is TLS disabled or single unit cluster` are also executed.

## On Create Backup Hook
[Click to navigate the mermaid diagram on GitHub](https://github.com/canonical/postgresql-k8s-operator/blob/main/docs/explanation/e-backups.md).

```mermaid
flowchart TD
  hook_fired([create-backup Hook]) --> is_blocked{Is unit in\nblocked state?}
  is_blocked -- yes --> fail_action([fail action])
  is_blocked -- no --> is_primary_tls_enabled_multiple_unit_cluster{Is primary in\na TLS enabled\nmultiple units cluster?}
  is_primary_tls_enabled_multiple_unit_cluster -- yes --> fail_action
  is_primary_tls_enabled_multiple_unit_cluster -- no --> is_replica_tls_disabled{Is replica and\nwith TLS disabled?}
  is_replica_tls_disabled -- yes --> fail_action
  is_replica_tls_disabled -- no --> has_stanza_been_initialises{Has stanza been initialised?}
  has_stanza_been_initialises -- no --> fail_action
  has_stanza_been_initialises -- yes --> is_s3_relation_established{Is S3 relation\nestablished?}
  is_s3_relation_established -- no --> fail_action
  is_s3_relation_established -- yes --> has_missing_s3_parameters{Has missing S3 parameters?}
  has_missing_s3_parameters -- yes --> fail_action
  has_missing_s3_parameters -- no --> was_possible_upload_metadata_file_test_connectivity_s3{Was it possible\nto upload metadata file\nto test connectivity to S3?}
  was_possible_upload_metadata_file_test_connectivity_s3 -- no --> fail_action
  was_possible_upload_metadata_file_test_connectivity_s3 -- yes --> is_replica{Is current\nunit a replica?}
  is_replica -- no --> set_maintenance[Set Maintenance Status]
  is_replica -- yes --> block_new_connections[Block new\nconnections to this\nunit's database]
  block_new_connections --> set_maintenance
  set_maintenance --> has_backup_creation_succeeded{Has backup creation succeeded?}
  has_backup_creation_succeeded -- no --> upload_error_logs_s3[Upload error\nlogs to S3]
  upload_error_logs_s3 --> fail_action2([fail action])
  fail_action2 --> is_replica2{Is current\nunit a replica?}
  is_replica2 -- no --> set_active[Set Active Status]
  is_replica2 -- yes --> allow_new_connections[Allow new\nconnections to this\nunit's database]
  has_backup_creation_succeeded -- yes --> were_backup_logs_uploaded_s3{Were backup logs\nuploaded to S3?}
  were_backup_logs_uploaded_s3 -- no --> fail_action2
  were_backup_logs_uploaded_s3 -- yes --> finish_action[backup created]
  finish_action --> is_replica2
```

## On List Backups Hook
[Click to navigate the mermaid diagram on GitHub](https://github.com/canonical/postgresql-k8s-operator/blob/main/docs/explanation/e-backups.md).

```mermaid
flowchart TD
  hook_fired([list-backups Hook]) --> is_s3_relation_established{Is S3 relation\nestablished?}
  is_s3_relation_established -- no --> fail_action([fail action])
  is_s3_relation_established -- yes --> has_missing_s3_parameters{Has missing S3 parameters?}
  has_missing_s3_parameters -- yes --> fail_action
  has_missing_s3_parameters -- no --> does_pgbackrest_returned_backups_list{Does pgBackRest\nreturned the\nbackups list?}
  does_pgbackrest_returned_backups_list -- no --> fail_action
  does_pgbackrest_returned_backups_list -- yes --> return_formatted_backup_list[Return formatted\nbackup list]
```

## On Restore Hook
[Click to navigate the mermaid diagram on GitHub](https://github.com/canonical/postgresql-k8s-operator/blob/main/docs/explanation/e-backups.md).

```mermaid
flowchart TD
  hook_fired([restore Hook]) --> has_user_provided_backup_id{Has user provided\na backup id?}
  has_user_provided_backup_id -- no --> fail_action([fail action])
  has_user_provided_backup_id -- yes --> is_workload_container_accessible{Is workload\ncontainer accessible?}
  is_workload_container_accessible -- no --> fail_action
  is_workload_container_accessible -- yes --> is_blocked{Is unit in\nblocked state?}
  is_blocked -- yes --> fail_action
  is_blocked -- no --> is_single_unit_cluster{Is single\nunit cluster?}
  is_single_unit_cluster -- no --> fail_action
  is_single_unit_cluster -- yes --> is_leader{Is current\nunit leader?} 
  is_leader -- no --> fail_action
  is_leader -- yes -->  is_backup_id_valid{Is backup id\nvalid?}
  is_backup_id_valid -- no --> fail_action
  is_backup_id_valid -- yes --> set_maintenance[Set Maintenance Status]
  set_maintenance --> has_database_stopped{Has database\nstopped?}
  has_database_stopped -- no --> fail_action
  has_database_stopped -- yes --> was_previous_cluster_info_removed{Was previous cluster\ninfo removed?}
  was_previous_cluster_info_removed -- no --> start_database_again[Start database again]
  start_database_again --> fail_action
  was_previous_cluster_info_removed -- yes --> was_data_directory_emptied{Was the data\ndirectory emptied?}
  was_data_directory_emptied --> no --> start_database_again
  was_data_directory_emptied --> yes --> configure_restore[Configure Patroni to restore the backup]
  configure_restore --> start_database[Start the database]
  start_database --> finish_action[restore started]
```

The unit status becomes `Active` or `Blocked` after a, respectively, successful or failed restore
is detected in the update status hook.
