# How to deploy on AKS

[Azure Kubernetes Service](https://learn.microsoft.com/en-us/azure/aks/) (AKS) allows you to quickly deploy a production ready Kubernetes cluster in Azure. To access the AKS Web interface, go to [https://portal.azure.com/](https://portal.azure.com/).

## Summary
* [Install AKS and Juju tooling](#heading--install-aks-juju)
* [Create a new AKS cluster](#heading--create-aks-cluster)
* [Bootstrap Juju on AKS](#heading--boostrap-juju)
* [Deploy charms](#heading--deploy-charms)
* [Display deployment information](#heading--display-information)
* [Clean up](#heading--clean-up)

---

<a href="#heading--install-aks-juju"><h2 id="heading--install-aks-juju"> Install AKS and Juju tooling</h2></a>

Install Juju and Azure CLI tool:
```shell
sudo snap install juju --classic
sudo apt install --yes azure-cli
```
Follow the installation guides for:
* [az](https://learn.microsoft.com/en-us/cli/azure/what-is-azure-cli) - the Azure CLI

To check they are all correctly installed, you can run the commands demonstrated below with sample outputs:

```shell
~$ juju version
3.4.2-genericlinux-amd64

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
```shell
az login
```

<a href="#heading--create-aks-cluster"><h2 id="heading--create-aks-cluster"> Create a new AKS cluster</h2></a>

Export the deployment name for further use:
```shell
export JUJU_NAME=aks-$USER-$RANDOM
```

This following examples in this guide will use the single server AKS in location `eastus` - feel free to change this for your own deployment.

Create a new [Azure Resource Group](https://learn.microsoft.com/en-us/cli/azure/manage-azure-groups-azure-cli):

```shell
az group create --name aks --location eastus
```
Bootstrap AKS with the following command (increase nodes count/size if necessary):
```shell
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

Dump newly bootstraped AKS credentials:
```shell
az aks get-credentials --resource-group aks --name ${JUJU_NAME} --context aks
```

Sample output:
```shell
...
Merged "aks" as current context in ~/.kube/config
```

<a href="#heading--boostrap-juju"><h2 id="heading--boostrap-juju"> Bootstrap Juju on AKS</h2></a>

Bootstrap Juju controller:
```shell
juju bootstrap aks aks
```
Sample output:
```shell
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
```shell
juju add-model welcome aks
```
[Optional] Increase DEBUG level if you are troubleshooting charms:
```shell
juju model-config logging-config='<root>=INFO;unit=DEBUG'
```

<a href="#heading--deploy-charms"><h2 id="heading--deploy-charms">Deploy charms</h2></a>

The following command deploys PostgreSQL K8s:

> **Important**: AKS supported for charm revision 247+ currently in the channel `14/candidate` only.

```shell
juju deploy postgresql-k8s --trust -n 3 --channel 14/candidate
```
Sample output:
```shell
Deployed "postgresql-k8s" from charm-hub charm "postgresql-k8s", revision 247 in channel 14/candidate on ubuntu@22.04/stable
```

Check the status:
```shell
juju status --watch 1s
```
Sample output:
```shell
Model    Controller  Cloud/Region  Version  SLA          Timestamp
welcome  aks         aks/eastus    3.4.2    unsupported  17:53:35+02:00

App             Version  Status  Scale  Charm           Channel       Rev  Address       Exposed  Message
postgresql-k8s  14.11    active      3  postgresql-k8s  14/candidate  247  10.0.237.223  no       Primary

Unit               Workload  Agent  Address      Ports  Message
postgresql-k8s/0*  active    idle   10.244.0.19         Primary
postgresql-k8s/1   active    idle   10.244.0.18         
postgresql-k8s/2   active    idle   10.244.0.17  
```

<a href="#heading--display-information"><h2 id="heading--display-information"> Display deployment information</h2></a>

Display information about the current deployments with the following commands:
```shell
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

<a href="#heading--clean-up"><h2 id="heading--clean-up"> Clean up</h2></a>

[note type="caution"]
Always clean AKS resources that are no longer necessary -  they could be costly!
[/note]

To clean the AKS cluster, resources and juju cloud, run the following commands:

```shell
juju destroy-controller aks --destroy-all-models --destroy-storage --force
```
List all services and then delete those that have an associated EXTERNAL-IP value (load balancers, ...):
```shell
kubectl get svc --all-namespaces
kubectl delete svc <service-name> 
```
Next, delete the AKS resources (source: [Deleting an all Azure VMs]((https://learn.microsoft.com/en-us/cli/azure/delete-azure-resources-at-scale#delete-all-azure-resources-of-a-type) )) 
```shell
az aks delete -g aks -n ${JUJU_NAME}
```
Finally, logout from AKS to clean the local credentials (to avoid forgetting and leaking):
```shell
az logout
```