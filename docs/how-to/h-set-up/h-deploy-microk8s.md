# How to deploy on MicroK8s

[MicroK8s](https://microk8s.io/docs) is a lightweight Kubernetes engine created by Canonical. 

## Summary
* [MicroK8s on Multipass](#heading--multipass)
* [MicroK8s on other platforms](#heading--other-platforms)

---
<a href="#heading--multipass"><h2 id="heading--multipass"> MicroK8s on Multipass </h2></a>

The Charmed PostgreSQL K8s Tutorial contains detailed instructions to deploy PostgreSQL on MicroK8s and Multipass in the following pages:
* [1. Set up the environment](/t/9297) - set up Multipass and Juju
* [2. Deploy PostgreSQL](/t/9298) - deploy PostgresQL K8s in a Multipass instance

### Summary
Below is an example of the commands to deploy PostgreSQL K8s on MicroK8s running inside a Multipass VM from scratch on Ubuntu 22.04 LTS:

```shell
sudo snap install multipass
multipass launch --cpus 4 --memory 8G --disk 30G --name my-vm charm-dev
multipass shell my-vm

juju add-model example
juju deploy postgresql-k8s --trust
```

Example `juju status` output:
```shell
Model       Controller  Cloud/Region        Version  SLA          Timestamp
example  charm-dev   microk8s/localhost  2.9.42   unsupported  12:00:43+01:00

App             Version  Status  Scale  Charm           Channel    Rev  Address         Exposed  Message
postgresql-k8s           active      1  postgresql-k8s  14/stable  56   10.152.183.167  no

Unit               Workload  Agent  Address       Ports  Message
postgresql-k8s/0*  active    idle   10.1.188.206
```

<a href="#heading--other-platforms"><h2 id="heading--other-platforms"> MicroK8s on other platforms </h2></a>

MicroK8s can be installed on a multitude of platforms and environments for different use cases. See all options and details in the [official documentation](https://microk8s.io/docs/install-alternatives).

[note type="caution"]
Not all platforms supported by MicroK8s will work with this charm - keep in mind the [system requirements](/t/11744) of Charmed PostgreSQL.
[/note]

## Test your deployment
Check the [Testing](/t/11774) reference to test your deployment.