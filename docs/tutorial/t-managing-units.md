# Scale your Charmed PostgreSQL

This is part of the [Charmed PostgreSQL Tutorial](/t/charmed-postgresql-k8s-tutorial-overview/9296?channel=14/stable). Please refer to this page for more information and the overview of the content.

## Adding and Removing units

Charmed PostgreSQL K8s operator uses [PostgreSQL Patroni-based cluster](https://patroni.readthedocs.io/en/latest/) for scaling. It provides features such as automatic membership management, fault tolerance, automatic failover, and so on. The charm uses Postgres’s [Synchronous replication](https://patroni.readthedocs.io/en/latest/replication_modes.html#postgresql-k8s-synchronous-replication) with Patroni.

> **!** *Disclaimer: this tutorial hosts replicas all on the same machine, this should not be done in a production environment. To enable high availability in a production environment, replicas should be hosted on different servers to [maintain isolation](https://canonical.com/blog/database-high-availability).*

### Add cluster members (replicas)
You can add two replicas to your deployed PostgreSQL application by scaling it to three units using:
```shell
juju scale-application postgresql-k8s 3
```

You can now watch the scaling process in live using: `juju status --watch 1s`. It usually takes several minutes for new cluster members to be added. You’ll know that all three nodes are in sync when `juju status` reports `Workload=active` and `Agent=idle`:
```
Model     Controller  Cloud/Region        Version  SLA          Timestamp
tutorial  charm-dev   microk8s/localhost  2.9.42   unsupported  12:09:49+01:00

App             Version  Status  Scale  Charm           Channel    Rev  Address         Exposed  Message
postgresql-k8s           active      3  postgresql-k8s  14/stable  56   10.152.183.167  no

Unit               Workload  Agent  Address       Ports  Message
postgresql-k8s/0*  active    idle   10.1.188.206         Primary
postgresql-k8s/1   active    idle   10.1.188.209
postgresql-k8s/2   active    idle   10.1.188.210
```

### Remove cluster members (replicas)
Removing a unit from the application, scales the replicas down. Before we scale down the replicas, list all the units with `juju status`, here you will see three units `postgresql-k8s/0`, `postgresql-k8s/1`, and `postgresql-k8s/2`. Each of these units hosts a PostgreSQL replica. To scale the application down to two units, enter:
```shell
juju scale-application postgresql-k8s 2
```

You’ll know that the replica was successfully removed when `juju status --watch 1s` reports:
```
Model     Controller  Cloud/Region        Version  SLA          Timestamp
tutorial  charm-dev   microk8s/localhost  2.9.42   unsupported  12:10:08+01:00

App             Version  Status  Scale  Charm           Channel    Rev  Address         Exposed  Message
postgresql-k8s           active      2  postgresql-k8s  14/stable  56   10.152.183.167  no

Unit               Workload  Agent  Address       Ports  Message
postgresql-k8s/0*  active    idle   10.1.188.206         Primary
postgresql-k8s/1   active    idle   10.1.188.209
```