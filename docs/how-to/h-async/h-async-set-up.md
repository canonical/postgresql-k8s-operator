# Set up clusters for cross-regional async replication

Cross-regional (or multi-server) asynchronous replication focuses on disaster recovery by distributing data across different servers. 

This guide will show you the basics of initiating a cross-regional async setup using an example PostgreSQL K8s deployment with two servers: one in Rome and one in Lisbon.

## Summary
* [Deploy](#deploy)
* [Offer](#offer)
* [Consume](#consume)
* [Promote or switchover a cluster](#promote-or-switchover-a-cluster)
* [Scale a cluster](#scale-a-cluster)

---

## Deploy

To deploy two clusters in different servers, create two juju models - one for the `rome` cluster, one for the `lisbon` cluster. In the example below, we use the config flag `profile=testing` to limit memory usage.

```shell
juju add-model rome 
juju add-model lisbon

juju switch rome # active model must correspond to cluster
juju deploy postgresql-k8s db1 --trust --channel=14/edge --config profile=testing --base ubuntu@22.04

juju switch lisbon
juju deploy postgresql-k8s db2 --trust --channel=14/edge --config profile=testing --base ubuntu@22.04
```

## Offer

[Offer](https://juju.is/docs/juju/offer) asynchronous replication in one of the clusters.

```shell
juju switch rome
juju offer db1:replication-offer replication-offer
``` 

## Consume

Consume asynchronous replication on planned `Standby` cluster (Lisbon):
```shell
juju switch lisbon
juju consume rome.replication-offer
juju integrate replication-offer db2:replication
``` 

## Promote or switchover a cluster

To define the primary cluster, use the `create-replication` action.

```shell
juju run -m rome db1/leader create-replication
```

To switchover and use `lisbon` as the primary instead, run

```shell
juju run -m lisbon db2/leader promote-to-primary
```

## Scale a cluster

The two clusters work independently, which means that it’s possible to scale each cluster separately. The `-m` flag defines the target of this action, so it can be performed within any active model. 

```shell
juju scale-application db1 3 -m rome

juju scale-application db2 3 -m lisbon
``` 

[note]
**Note:** Scaling is possible before and after the asynchronous replication is established/created.
[/note]