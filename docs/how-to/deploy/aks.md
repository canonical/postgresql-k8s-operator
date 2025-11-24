# How to deploy on AKS

[Azure Kubernetes Service](https://learn.microsoft.com/en-us/azure/aks/) (AKS) allows you to quickly deploy a production ready Kubernetes cluster in Azure. To access the AKS Web interface, go to [https://portal.azure.com/](https://portal.azure.com/).

## Prerequisites

This guide assumes you have:

* A physical or virtual machine running Ubuntu 22.04+
* Juju 3 (`3.6+` is recommended)
  * See: [How to install Juju](https://documentation.ubuntu.com/juju/3.6/howto/manage-juju/#install-juju)

## Install AKS tooling

Install the Azure CLI tool:

```text
sudo apt install --yes azure-cli
```

Follow the installation guide for the [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/what-is-azure-cli).

To check it is all correctly installed, you can run the commands demonstrated below with sample outputs:

```text
~$ az --version
azure-cli                         2.61.0

core                              2.61.0
telemetry                          1.1.0

Dependencies:
msal                              1.28.0
azure-mgmt-resource               23.1.1
...
Your CLI is up-to-date.
```

### Authenticate

Login to your Azure account:

```text
az login
```

## Create a new AKS cluster

Export the deployment name for further use:
```text
export JUJU_NAME=aks-$USER-$RANDOM
```

This following examples in this guide will use the single server AKS in location `eastus` - feel free to change this for your own deployment.

Create a new [Azure Resource Group](https://learn.microsoft.com/en-us/cli/azure/manage-azure-groups-azure-cli):

```text
az group create --name aks --location eastus
```
Bootstrap AKS with the following command (increase nodes count/size if necessary):
```text
az aks create -g aks -n ${JUJU_NAME} --enable-managed-identity --node-count 1 --node-vm-size=Standard_D4s_v4 --generate-ssh-keys
```

Sample output:
```yaml
{
  "aadProfile": null,
  "addonProfiles": null,
  "agentPoolProfiles": [
    {
      "availabilityZones": null,
      "capacityReservationGroupId": null,
      "count": 1,
      "creationData": null,
      "currentOrchestratorVersion": "1.28.9",
      "enableAutoScaling": false,
      "enableEncryptionAtHost": false,
      "enableFips": false,
      "enableNodePublicIp": false,
...
```

Dump newly bootstrapped AKS credentials:
```text
az aks get-credentials --resource-group aks --name ${JUJU_NAME} --context aks
```

Sample output:
```text
...
Merged "aks" as current context in ~/.kube/config
```

## Bootstrap Juju on AKS

Bootstrap Juju controller:
```text
juju bootstrap aks aks
```
Sample output:
```text
Creating Juju controller "aks" on aks/eastus
Bootstrap to Kubernetes cluster identified as azure/eastus
Creating k8s resources for controller "controller-aks"
Downloading images
Starting controller pod
Bootstrap agent now started
Contacting Juju controller at 20.231.233.33 to verify accessibility...

Bootstrap complete, controller "aks" is now available in namespace "controller-aks"

Now you can run
	juju add-model <model-name>
to create a new model to deploy k8s workloads.
```

Create a new Juju model (k8s namespace)
```text
juju add-model welcome aks
```
[Optional] Increase DEBUG level if you are troubleshooting charms:
```text
juju model-config logging-config='<root>=INFO;unit=DEBUG'
```

## Deploy charms

The following command deploys PostgreSQL K8s:

```text
juju deploy postgresql-k8s --channel 16/edge --trust -n 3
```
Sample output:
```text
Deployed "postgresql-k8s" from charm-hub charm "postgresql-k8s", revision <number> in channel 16/edge on ubuntu@24.04/edge
```

Check the status:
```text
juju status --watch 1s
```
Sample output:
```text
Model    Controller  Cloud/Region  Version  SLA          Timestamp
welcome  aks         aks/eastus    3.4.2    unsupported  17:53:35+02:00

App             Version  Status  Scale  Charm           Channel       Rev  Address       Exposed  Message
postgresql-k8s  14.11    active      3  postgresql-k8s  16/edge       615  10.0.237.223  no       Primary

Unit               Workload  Agent  Address      Ports  Message
postgresql-k8s/0*  active    idle   10.244.0.19         Primary
postgresql-k8s/1   active    idle   10.244.0.18         
postgresql-k8s/2   active    idle   10.244.0.17  
```

## Display deployment information

Display information about the current deployments with the following commands:
```text
~$ kubectl cluster-info 
Kubernetes control plane is running at https://aks-user-aks-aaaaa-bbbbb.hcp.eastus.azmk8s.io:443
CoreDNS is running at https://aks-user-aks-aaaaa-bbbbb.hcp.eastus.azmk8s.io:443/api/v1/namespaces/kube-system/services/kube-dns:dns/proxy
Metrics-server is running at https://aks-user-aks-aaaaa-bbbbb.hcp.eastus.azmk8s.io:443/api/v1/namespaces/kube-system/services/https:metrics-server:/proxy

~$ az aks list
...
        "count": 1,
        "currentOrchestratorVersion": "1.28.9",
        "enableAutoScaling": false,
...

~$ kubectl get node
NAME                                STATUS   ROLES   AGE   VERSION
aks-nodepool1-31246187-vmss000000   Ready    agent   11m   v1.28.9
```

## Clean up

```{caution}
Always clean AKS resources that are no longer necessary -  they could be costly!
```

To clean the AKS cluster, resources and juju cloud, run the following commands:

```text
juju destroy-controller aks --destroy-all-models --destroy-storage --force
```

List all services and then delete those that have an associated EXTERNAL-IP value (load balancers, ...):

```text
kubectl get svc --all-namespaces
kubectl delete svc <service-name> 
```

Next, delete the AKS resources (source: [Deleting an all Azure VMs](https://learn.microsoft.com/en-us/cli/azure/delete-azure-resources-at-scale#delete-all-azure-resources-of-a-type)) 

```text
az aks delete -g aks -n ${JUJU_NAME}
```

Finally, logout from AKS to clean the local credentials (to avoid forgetting and leaking):
```text
az logout
```

