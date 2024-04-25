# Relations flowcharts

This reference documentation details the implementation of the `database-peers` peer relation. This is the peer relation for PostgreSQL, used to share user and config information from the leader unit to the follower units. The file implementing these relations can be found here: [src/relations/charm.py](https://github.com/canonical/postgresql-k8s-operator/blob/main/src/charm.py) (it should be moved to a file called `src/relations/peers.py` in the future).

## Expected Interface

These are the expected contents of the databags in this relation (all values are examples, generated in a running test instance):

## Hook Handler Flowcharts

These flowcharts detail the control flow of the hooks in this program. Unless otherwise stated, **a hook deferral is always followed by a return**.

## Peer Relation Changed Hook
[Click to navigate the mermaid diagram on GitHub](https://github.com/canonical/postgresql-k8s-operator/blob/main/docs/explanation/e-peers.md).

```mermaid
flowchart TD
  hook_fired([peer-relation-changed Hook]) --> has_cluster_initialised{Has cluster\n initialised?}
  has_cluster_initialised -- no --> defer>defer]
  has_cluster_initialised -- yes --> is_leader{Is current\nunit leader?}
  is_leader -- no --> is_part_of_cluster{Is current unit \n part of the cluster?}
  is_leader -- yes --> all_units_on_cluster{Are all the units \n part of the cluster?}
  all_units_on_cluster -- yes --> is_part_of_cluster
  all_units_on_cluster -- no --> are_all_members_ready{Are all cluster \n members ready?}

  %% This defer should have a return after it to stop the execution.
  are_all_members_ready -- no --> defer2>defer]
  defer2 --> is_part_of_cluster

  are_all_members_ready -- yes --> add_unit_to_cluster[Add unit to cluster]
  add_unit_to_cluster --> patch_pod_labels[Patch pod labels of the new cluster member]
  patch_pod_labels --> all_units_on_cluster
  is_part_of_cluster -- no --> rtn([return])
  is_part_of_cluster -- yes --> update_config[Update Patroni and \n PostgreSQL config \n]
  update_config --> restart_postgresql[Restart PostgreSQL \n if TLS is turned on/off]
  restart_postgresql --> has_member_started{Have Patroni and \n PostgreSQL started in \n the current unit?}
  has_member_started -- no --> defer3>defer]

  %% Here also the legacy relations' standby field should be updated.
  has_member_started -- yes --> update_read_only_endpoint[Update the read-only endpoint \n in the database relation]
  update_read_only_endpoint --> set_active[Set Active\n Status]

  set_active --> rtn2([return])
```

## Peer Relation Departed Hook
[Click to navigate the mermaid diagram on GitHub](https://github.com/canonical/postgresql-k8s-operator/blob/main/docs/explanation/e-peers.md).

```mermaid
flowchart TD
  hook_fired([peer-relation-changed Hook]) --> is_leader_and_is_not_departing{Is leader and \n is not departing?}
  is_leader_and_is_not_departing -- no --> rtn([return])
  is_leader_and_is_not_departing -- yes --> has_cluster_initialised{Has cluster\n initialised?}
  has_cluster_initialised -- no --> defer>defer]

  %% Here also the legacy relations' standby field should be updated.
  has_cluster_initialised -- yes --> update_read_only_endpoint[Update the read-only endpoint \n in the database relation]
  update_read_only_endpoint --> remove_departing_units[Remove departing units \n from the cluster]
  remove_departing_units --> update_config[Turn on/off PostgreSQL \n synchronous_commit configuration]
  update_config --> rtn2([return])
```
