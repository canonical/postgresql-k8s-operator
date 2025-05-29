


# How to deploy

This page aims to provide an introduction to the PostgreSQL deployment process and lists all the related guides. It contains the following sections:
* [General deployment instructions](#general-deployment-instructions)
* [Clouds](#clouds)
* [Special deployments](#special-deployments)

---

## General deployment instructions

The basic requirements for deploying a charm are the [**Juju client**](https://juju.is/docs/juju) and a Kubernetes [**cloud**](https://juju.is/docs/juju/cloud).

First, [bootstrap](https://juju.is/docs/juju/juju-bootstrap) the cloud controller and create a [model](https://canonical-juju.readthedocs-hosted.com/en/latest/user/reference/model/): 
```shell
juju bootstrap <cloud name> <controller name>
juju add-model <model name>
```

Then, either continue with the `juju` client **or** use the `terraform juju` client to deploy the PostgreSQL charm.

To deploy with the `juju` client:
```shell
juju deploy postgresql-k8s --trust
```
> See also: [`juju deploy` command](https://canonical-juju.readthedocs-hosted.com/en/latest/user/reference/juju-cli/list-of-juju-cli-commands/deploy/)

To deploy with `terraform juju`, follow the guide [How to deploy using Terraform].
> See also: [Terraform Provider for Juju documentation](https://canonical-terraform-provider-juju.readthedocs-hosted.com/en/latest/)

If you are not sure where to start or would like a more guided walkthrough for setting up your environment, see the [Charmed PostgreSQL K8s tutorial][Tutorial].

## Clouds

The guides below go through the steps to install different cloud services and bootstrap them to Juju:
* [Canonical K8s]
* [Google Kubernetes Engine]
* [Amazon Elastic Kubernetes Service]
* [Azure Kubernetes Service]

[How to deploy on multiple availability zones (AZ)] demonstrates how to deploy a cluster on a cloud using different AZs for high availability.

## Special deployment scenarios

These guides cover some specific deployment scenarios and configurations.

### External network access 

See [How to connect from outside the local network] for guidance on connecting with a client application outside PostgreSQL's Kubernetes cluster. 

### Airgapped
[How to deploy in an offline or air-gapped environment] goes over the special configuration steps for installing PostgreSQL in an airgapped environment via CharmHub and the Snap Store Proxy.

### Cluster-cluster replication
Cluster-cluster, cross-regional, or multi-server asynchronous replication focuses on disaster recovery by distributing data across different servers. 

The [Cross-regional async replication] guide goes through the steps to set up clusters for cluster-cluster replication, integrate with a client, and remove or recover a failed cluster.

[Tutorial]: /tutorial/index

[How to deploy using Terraform]: /how-to-guides/deploy/terraform

[Canonical K8s]: /how-to-guides/deploy/canonical-k8s
[Google Kubernetes Engine]: /how-to-guides/deploy/gke
[Amazon Elastic Kubernetes Service]: /how-to-guides/deploy/eks
[Azure Kubernetes Service]: /how-to-guides/deploy/aks

[How to deploy on multiple availability zones (AZ)]: /how-to-guides/deploy/multi-az

[How to enable TLS]: /how-to-guides/enable-tls
[How to connect from outside the local network]: /how-to-guides/external-network-access

[How to deploy in an offline or air-gapped environment]: /how-to-guides/deploy/air-gapped
[Cross-regional async replication]: /how-to-guides/cross-regional-async-replication/index


```{toctree}
:titlesonly:
:maxdepth: 2
:glob:
:hidden:

*
*/index
