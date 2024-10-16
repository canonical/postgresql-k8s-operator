# How to deploy on MicroK8s

This guide assumes you have a running Juju and  [MicroK8s](https://microk8s.io/docs) environment. 

For a detailed walkthrough of setting up an environment and deploying the charm on MicroK8s, refer to the following Tutorial pages:
* [1. Set up the environment](/t/9297) - set up Multipass and Juju
* [2. Deploy PostgreSQL](/t/9298) - deploy PostgresQL K8s in a Multipass instance

MicroK8s can be installed on a multitude of platforms and environments for different use cases. See all options and details in the [official documentation](https://microk8s.io/docs/install-alternatives).

[note type="caution"]
Not all platforms supported by MicroK8s will work with this charm - keep in mind the [system requirements](/t/11744) of Charmed PostgreSQL.
[/note]

## Prerequisites
* Canonical MicroK8s 1.27+
* Fulfill the general [system requirements](/t/11744)

---

[Bootstrap](https://juju.is/docs/juju/juju-bootstrap) a juju controller and create a [model](https://juju.is/docs/juju/juju-add-model) if you haven't already:
```shell
juju bootstrap localhost <controller name>
juju add-model <model name>
```

Deploy PostgreSQL K8s:

```shell
juju deploy postgresql-k8s --trust
```
> :warning: The `--trust` flag is necessary to create some K8s resources

> See the [`juju deploy` documentation](https://juju.is/docs/juju/juju-deploy) for all available options at deploy time.
> 
> See the [Configurations tab](https://charmhub.io/postgresql-k8s/configurations) for specific PostgreSQL K8s parameters.

Example `juju status --wait 1s` output:
```shell
Model       Controller  Cloud/Region        Version  SLA          Timestamp
example  charm-dev   microk8s/localhost  2.9.42   unsupported  12:00:43+01:00

App             Version  Status  Scale  Charm           Channel    Rev  Address         Exposed  Message
postgresql-k8s           active      1  postgresql-k8s  14/stable  56   10.152.183.167  no

Unit               Workload  Agent  Address       Ports  Message
postgresql-k8s/0*  active    idle   10.1.188.206
```