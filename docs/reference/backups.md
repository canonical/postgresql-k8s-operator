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
```

### On List Backups Hook

```mermaid
flowchart TD
```

### On Restore Hook

```mermaid
flowchart TD
```
