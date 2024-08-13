> [Charmed PostgreSQL K8s Tutorial](/t/9296) >  4. Scale replicas

# Scale your replicas
The Charmed PostgreSQL K8s operator uses a [PostgreSQL Patroni-based cluster](https://patroni.readthedocs.io/en/latest/) for scaling. It provides features such as automatic membership management, fault tolerance, and automatic failover. The charm uses PostgreSQL’s [synchronous replication](https://patroni.readthedocs.io/en/latest/replication_modes.html#postgresql-k8s-synchronous-replication) with Patroni.

In this section, you will learn to scale your Charmed PostgreSQL by adding or removing units (replicas).

[note type="caution"]
This tutorial hosts all replicas on the same machine. **This should not be done in a production environment.**

To enable high availability in a production environment, replicas should be hosted on different servers to [maintain isolation](https://canonical.com/blog/database-high-availability).
[/note]

## Summary

- [Add units](#heading--add-units)
- [Remove units](#heading--remove-units)

---
<a href="#heading--add-units"><h2 id="heading--add-units"> Add units </h2></a>

Currently, your deployment has only one juju **unit**, known in juju as the **leader unit**. You can think of this as the database **primary instance**. For each **replica**, a new unit is created. All units are members of the same database cluster.

To add two replicas to your deployed PostgreSQL application, use `juju scale-application` to scale it to three units:
```shell
juju scale-application postgresql-k8s 3
```
[note]
**Note**: Unlike machine models, Kubernetes models use `juju scale-application` instead of `juju add-unit` and `juju remove-unit`. For more information about juju's scaling logic for kubernetes, check [this post](https://discourse.charmhub.io/t/adding-removing-units-scale-application-command/153).
[/note]

You can now watch the scaling process in live using: `juju status --watch 1s`. It usually takes several minutes for new cluster members to be added. 

You’ll know that all three nodes are in sync when `juju status` reports `Workload=active` and `Agent=idle`:
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

<a href="#heading--remove-units"><h2 id="heading--remove-units"> Remove units </h2></a>

Removing a unit from the application scales down  the replicas.

Before we scale them down, list all the units with `juju status`. You will see three units:  `postgresql-k8s/0`, `postgresql-k8s/1`, and `postgresql-k8s/2`. Each of these units hosts a PostgreSQL replica. 

To scale the application down to two units, enter:
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

**Next step:** [5. Manage passwords](/t/9300)