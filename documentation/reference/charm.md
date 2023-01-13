# Charm.py Reference Documentation

This file is the entrypoint for the charm, and contains functions for its basic operation, including its major hooks and file management. This file can be found at [src/charm.py](../../../src/charm.py).

## Hook Handler Flowcharts

These flowcharts detail the control flow of the hooks in this program. Unless otherwise stated, **a hook deferral is always followed by a return**.

### Leader Elected Hook

```mermaid
flowchart TD
  hook_fired([leader-elected Hook]) --> generate_passwords{Generate password for charm users?}
  generate_passwords --> create_k8s_resources[Create k8s resources needed by Patroni]
  create_k8s_resources --> is_part_of_cluster{Is current unit \n part of the cluster?}
  is_part_of_cluster -- no --> add_to_cluster[Add current unit \n to the cluster]
  add_to_cluster --> remove_departed_units
  is_part_of_cluster -- yes --> remove_departed_units[Remove from the cluster \n pending departed units]
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

### PostgreSQL Pebble Ready Hook

