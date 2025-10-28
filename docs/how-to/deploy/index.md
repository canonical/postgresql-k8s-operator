(deploy)=
# How to deploy

The basic requirements for deploying a charm are the [**Juju client**](https://documentation.ubuntu.com/juju/3.6/) and a machine [**cloud**](https://juju.is/docs/juju/cloud).

For more details, see {ref}`system-requirements`.

If you are not sure where to start, or would like a more guided walkthrough for setting up your environment, see the {ref}`tutorial`.

## Quickstart

First, [bootstrap](https://juju.is/docs/juju/juju-bootstrap) the cloud controller and create a [model](https://canonical-juju.readthedocs-hosted.com/en/latest/user/reference/model/):

```shell
juju bootstrap <cloud name> <controller name>
juju add-model <model name>
```

Then, use the [`juju deploy`](https://canonical-juju.readthedocs-hosted.com/en/latest/user/reference/juju-cli/list-of-juju-cli-commands/deploy/) command:

```shell
juju deploy postgresql-k8s --channel 16/edge -n <number_of_replicas> --trust
```

If you are not sure where to start or would like a more guided walkthrough for setting up your environment, see the {ref}`tutorial`.

(deploy-clouds)=
## Clouds

Set up different cloud services for a Charmed PostgreSQL deployment:

```{toctree}
:titlesonly:

Canonical K8s <canonical-k8s>
GKE <gke>
EKS <eks>
AKS <aks>
```

Deploy a cluster on a cloud using different availability zones:

```{toctree}
:titlesonly:

Multi-AZ <multi-az>
```

## Terraform

Deploy PostgreSQL and automate your infrastructure with the Juju Terraform Provider:

```{toctree}
:titlesonly:

Terraform <terraform>
```

Air-gapped <air-gapped>

## Airgapped

Install PostgreSQL in an airgapped environment via Charmhub and the Snap Store Proxy:

```{toctree}
:titlesonly:

Air-gapped <air-gapped>
```
