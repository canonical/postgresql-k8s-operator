# How to deploy on Canonical Kubernetes

[Canonical Kubernetes](https://ubuntu.com/kubernetes) is a Kubernetes service built on Ubuntu and optimised for most major public clouds. 

The following instructions are a summarised version of the steps for installing Canonical K8s. For more thorough instructions and details, see the official Canonical Kubernetes documentation: [Install Canonical Kubernetes from a snap](https://documentation.ubuntu.com/canonical-kubernetes/latest/src/snap/howto/install/snap/).

## Prerequisites

This guide assumes you have:

* A physical or virtual machine running Ubuntu 22.04+
* Juju 3 (`3.6+` is recommended)
  * See: [How to install Juju](https://documentation.ubuntu.com/juju/3.6/howto/manage-juju/#install-juju)

## Install Canonical Kubernetes

Install, bootstrap, and check the status of Canonical K8s:

```text
sudo snap install k8s --edge --classic
sudo k8s bootstrap
sudo k8s status --wait-ready
```

Once Canonical K8s is up and running, [enable local storage](https://documentation.ubuntu.com/canonical-kubernetes/latest/snap/tutorial/getting-started/#enable-local-storage) (or any another persistent volume provider, to be used by [Juju Storage](https://juju.is/docs/juju/storage) later):
```text
sudo k8s enable local-storage
sudo k8s status --wait-ready
```

(Optional) Install the `kubectl` tool and dump the K8s config:
```text
sudo snap install kubectl --classic
mkdir ~/.kube
sudo k8s config > ~/.kube/config
kubectl get namespaces # to test the credentials
```

## Bootstrap a controller

Bootstrap the first Juju controller in K8s:

```text
juju add-k8s ck8s --client --context-name="k8s"
juju bootstrap ck8s
```

## Deploy Charmed PostgreSQL K8s

```text
juju add-model postgresql
juju deploy postgresql-k8s --channel 16/edge --trust
```

follow the deployment progress using:
```text
juju status --watch 1s
```

Example output:
```text
Model       Controller  Cloud/Region  Version  SLA          Timestamp
postgresql  ck8s        ck8s          3.6-rc1  unsupported  17:25:11+01:00

App             Version   Status  Scale  Charm           Channel     Rev  Address         Exposed  Message
postgresql-k8s  14.12     active      1  postgresql-k8s  16/edge     615  10.152.183.30   no       

Unit               Workload  Agent  Address    Ports  Message
postgresql-k8s/0*  active    idle   10.1.0.16         Primary
```

