# Charm flowcharts

The file `charm.py` is the entrypoint for the charm. It contains functions for its basic operation, including its major hooks and file management. This file can be found at [src/charm.py](https://github.com/canonical/postgresql-k8s-operator/blob/main/src/charm.py).

## Hook Handler Flowcharts

These flowcharts detail the control flow of the hooks in this program. Unless otherwise stated, **a hook deferral is always followed by a return**.

## Leader Elected Hook
[Click to navigate the mermaid diagram on GitHub](https://github.com/canonical/postgresql-k8s-operator/blob/main/docs/explanation/e-charm.md).

```mermaid
flowchart TD
  hook_fired([leader-elected Hook]) --> generate_passwords{Generate password for charm users?}
  generate_passwords --> create_k8s_resources[Create k8s resources\n needed by Patroni]
  create_k8s_resources --> is_part_of_cluster{Is current unit \n part of the cluster?}
  is_part_of_cluster -- no --> add_to_cluster[Add current unit \n to the cluster]
  add_to_cluster --> remove_departed_units
  is_part_of_cluster -- yes --> remove_departed_units[Remove pending departed \n units from the cluster]
  remove_departed_units --> has_cluster_initialised{Has cluster\n initialised?}
  has_cluster_initialised -- no --> rtn([return])
  has_cluster_initialised -- yes --> all_units_on_cluster{Are all the units \n part of the cluster?}
  all_units_on_cluster -- yes --> update_config[Turn on/off PostgreSQL \n synchronous_commit configuration]
  all_units_on_cluster -- no --> are_all_members_ready{Are all cluster \n members ready?}

  %% This defer should have a return after it to stop the execution.
  are_all_members_ready -- no --> defer2>defer]
  defer2 --> update_config

  are_all_members_ready -- yes --> add_unit_to_cluster[Add unit to cluster]
  add_unit_to_cluster --> patch_pod_labels[Patch pod labels of the new cluster member]
  patch_pod_labels --> all_units_on_cluster
  update_config --> rtn2([return])
```

## PostgreSQL Pebble Ready Hook
[Click to navigate the mermaid diagram on GitHub](https://github.com/canonical/postgresql-k8s-operator/blob/main/docs/explanation/e-charm.md).

```mermaid
flowchart TD
  hook_fired([leader-elected Hook]) --> create_pgdata{Create data\n directory}
  create_pgdata --> is_leader_or_has_cluster_initialised{Is current unit\n leader or has the \n cluster initialised?}
  is_leader_or_has_cluster_initialised -- no --> defer>defer]
  is_leader_or_has_cluster_initialised -- yes --> has_pushed_tls_files{Has successfully \n pushed TLS files?}
  has_pushed_tls_files -- no --> defer2>defer]
  has_pushed_tls_files -- yes --> has_services_changed{Have pebble \n services changed?}
  has_services_changed -- no --> has_member_started
  has_services_changed -- yes --> update_and_restart_service[Update and restart \n the PostgreSQL service]
  update_and_restart_service --> has_member_started{Have Patroni and \n PostgreSQL  started in \n the current unit?}
  has_member_started -- no --> defer3>defer]
  has_member_started -- yes --> is_leader{Is current\nunit leader?}
  is_leader -- yes --> has_patched_pod_labels{Has successfully \n patched pod labels of \n the new current unit}
  is_leader -- no --> update_config
  has_patched_pod_labels -- no --> set_blocked[Set Blocked\nstatus]
  set_blocked --> rtn([return])
  has_patched_pod_labels -- yes --> is_service_redirecting_traffic{Is custom k8s service \n redirecting traffic to \n primary pod?}
  is_service_redirecting_traffic -- no --> set_waiting[Set Waiting\nstatus]
  set_waiting --> defer4>defer]
  is_service_redirecting_traffic -- yes --> mark_cluster_as_initialised[Mark cluster as initialised]
  mark_cluster_as_initialised--> update_config[Turn on/off PostgreSQL \n synchronous_commit configuration]
  update_config --> set_active[Set Active\n Status]
  set_active --> rtn2([return])
```