# How to deploy on Canonical Kubernetes

[Canonical Kubernetes](https://ubuntu.com/kubernetes) is built on Ubuntu and combines security with optimal price-performance. Canonical is a Certified Kubernetes Service Provider, your trusted advisor for a successful cloud native strategy. This manual shows you simplicity of deploying Charmed PostgreSQL K8s to Canonical Kubernetes.

## Summary
Assuming you have a spare hardware/VMs running Ubuntu 22.04 LTS (or newer). The only necessary steps will be:
* [Install Canonical Kubernetes](#heading--install-canonical-k8s)
* [Install Juju](#heading--install-juju)
* [Deploy Charmed PostgreSQL K8s](#heading--deploy-postgresql)
---

<a href="#heading--install-canonical-k8s"><h2 id="heading--install-canonical-k8s">Install Canonical Kubernetes</h2></a>

Follow the [official and detailed guide](https://documentation.ubuntu.com/canonical-kubernetes/latest/src/snap/howto/install/snap/) or simply run:

```shell
sudo snap install k8s --edge --classic
sudo k8s bootstrap
sudo k8s status --wait-ready
```

Once Canonical K8s is up and running, [enable the local storage](https://documentation.ubuntu.com/canonical-kubernetes/latest/snap/tutorial/getting-started/#enable-local-storage) (or any another persistent volumes provider, to be used by [Juju Storage](https://juju.is/docs/juju/storage) later):
```shell
sudo k8s enable local-storage
sudo k8s status --wait-ready
```

(Optional) Install kubectl tool and dump the K8s config:
```shell
sudo snap install kubectl --classic
mkdir ~/.kube
sudo k8s config > ~/.kube/config
kubectl get namespaces # to test the credentials
```
<a href="#heading--install-juju"><h2 id="heading--install-juju">Install Juju</h2></a>

Install Juju and bootstrap the first Juju controller in K8s:
```shell
sudo snap install juju --channel 3.6/candidate
juju add-k8s ck8s --client --context-name="k8s"
juju bootstrap ck8s
```

<a href="#heading--deploy-postgresql"><h2 id="heading--deploy-postgresql">Deploy Charmed PostgreSQL K8s</h2></a>

```shell
juju add-model postgresql
juju deploy postgresql-k8s --trust
```

follow the deployment progress using:
```shell
juju status --watch 1s
```

Example output:
```shell
Model       Controller  Cloud/Region  Version  SLA          Timestamp
postgresql  ck8s        ck8s          3.6-rc1  unsupported  17:25:11+01:00

App             Version   Status  Scale  Charm           Channel     Rev  Address         Exposed  Message
postgresql-k8s  14.12     active      1  postgresql-k8s  14/stable   381  10.152.183.30   no       

Unit               Workload  Agent  Address    Ports  Message
postgresql-k8s/0*  active    idle   10.1.0.16         Primary
```

Follow the further steps in Tutorial [to scale your application](/t/9299), [relate with other applications](/t/9301) and [more](/t/9296)!