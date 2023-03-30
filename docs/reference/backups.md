# Backups.py Reference Documentation

This file contains functions for related to backups management, including its major hooks. This file can be found at [src/backpus.py](../../../src/backups.py).

## Hook Handler Flowcharts

These flowcharts detail the control flow of the hooks in this program. Unless otherwise stated, **a hook deferral is always followed by a return**.

### On S3 Credentials Changed Hook

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
  is_wal_archiving_to_s3_working -- no --> set_blocked2[Set Blocked\nstatus]
  set_blocked2 --> rtn
  is_wal_archiving_to_s3_working -- yes --> is_tls_disabled_or_single_unit_cluster{Is TLS disabled or\nsingle unit cluster}
  is_tls_disabled_or_single_unit_cluster -- yes --> stop_pgbackrest_tls_server[Stop pgBackRest\nTLS server]
  is_tls_disabled_or_single_unit_cluster -- no --> is_replica_and_tls_server_not_running_on_primary{Is current\nunit a replica\nand TLS server isn't\nrunning on primary?}
  is_replica_and_tls_server_not_running_on_primary -- yes --> rtn 
  stop_pgbackrest_tls_server --> rtn
  is_replica_and_tls_server_not_running_on_primary -- no --> start_pgbackrest_tls_server[Start pgBackRest\nTLS server]
  start_pgbackrest_tls_server --> rtn
```

### On Create Backup Hook

```mermaid
flowchart TD
  hook_fired([create-backup Hook]) --> 
```

### On List Backups Hook

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

### On Restore Hook

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
  is_backup_id_valid -- yes --> has_database_stopped{Has database\nstopped?}
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
