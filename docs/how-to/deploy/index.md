# How to deploy

This page introduces the PostgreSQL deployment process and lists all related guides.

## General deployment instructions

The basic requirements for deploying a charm are the [**Juju client**](https://juju.is/docs/juju) and a machine [**cloud**](https://juju.is/docs/juju/cloud).

First, [bootstrap](https://juju.is/docs/juju/juju-bootstrap) the cloud controller and create a [model](https://canonical-juju.readthedocs-hosted.com/en/latest/user/reference/model/): 
```text
juju bootstrap <cloud name> <controller name>
juju add-model <model name>
```

Then, either continue with the `juju` client **or** use the `terraform juju` client to deploy the PostgreSQL charm.

**To deploy with the `juju` client:**
```text
juju deploy postgresql-k8s --channel 16/stable --trust
```
> See also: [`juju deploy` command](https://canonical-juju.readthedocs-hosted.com/en/latest/user/reference/juju-cli/list-of-juju-cli-commands/deploy/)

**To deploy with `terraform juju`**, follow the guide [How to deploy using Terraform].
> See also: [Terraform Provider for Juju documentation](https://canonical-terraform-provider-juju.readthedocs-hosted.com/latest/)

If you are not sure where to start or would like a more guided walkthrough for setting up your environment, see the [](/tutorial/index).

## Clouds

How to bootstrap and configure different cloud services:
* [Canonical K8s]
* [Google Kubernetes Engine]
* [Amazon Elastic Kubernetes Service]
* [Azure Kubernetes Service]
* [How to deploy on multiple availability zones (AZ)]

## Networking

* [How to enable TLS]
* [How to connect from outside the local network]

## Airgapped

[How to deploy in an offline or air-gapped environment] goes over the special configuration steps for installing PostgreSQL in an airgapped environment via Charmhub and the Snap Store Proxy.

## Cluster-cluster replication

Cluster-cluster, cross-regional, or multi-server asynchronous replication focuses on disaster recovery by distributing data across different servers. 

The [Cross-regional async replication] guide goes through the steps to set up clusters for cluster-cluster replication, integrate with a client, and remove or recover a failed cluster.

[Tutorial]: /tutorial/index

[How to deploy using Terraform]: /how-to/deploy/terraform

[Canonical K8s]: /how-to/deploy/canonical-k8s
[Google Kubernetes Engine]: /how-to/deploy/gke
[Amazon Elastic Kubernetes Service]: /how-to/deploy/eks
[Azure Kubernetes Service]: /how-to/deploy/aks

[How to deploy on multiple availability zones (AZ)]: /how-to/deploy/multi-az

[How to enable TLS]: /how-to/enable-tls
[How to connect from outside the local network]: /how-to/external-network-access

[How to deploy in an offline or air-gapped environment]: /how-to/deploy/air-gapped
[Cross-regional async replication]: /how-to/cross-regional-async-replication/index


```{toctree}
:titlesonly:
:maxdepth: 2
:glob:
:hidden:

Canonical K8s <canonical-k8s>
Google Kubernetes Engine <gke>
Amazon Elastic Kubernetes Service <eks>
Azure Kubernetes Service <aks>
Multi-AZ <multi-az>
Terraform <terraform>
Air-gapped <air-gapped>
