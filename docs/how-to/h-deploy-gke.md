# How to deploy on GKE

[Google Kubernetes Engine](https://cloud.google.com/kubernetes-engine?hl=en) (GKE) is a highly scalable and fully automated Kubernetes service. To access the GKE Web interface, go to [console.cloud.google.com/compute](https://console.cloud.google.com/compute).

This guide will walk you through setting up a cluster and deploying PostgreSQL K8s on GKE.

## Summary
* [Install GKE and Juju tooling](#heading--install-gke-juju)
* [Create a new GKE cluster](#heading--create-gke-cluster)
* [Bootstrap Juju on GKE](#heading--boostrap-juju)
* [Deploy charms](#heading--deploy-charms)
* [List clusters and clouds](#heading--list-clusters-clouds)
* [Clean up](#heading--clean-up)

---

<a href="#heading--install-gke-juju"><h2 id="heading--install-gke-juju"> Install GKE and Juju tooling</h2></a>

Install `juju`, `kubectl`, and Google Cloud command-line tools using snap:

```shell
sudo snap install juju
sudo snap install kubectl --classic
sudo snap install google-cloud-cli --classic
```

### Authenticate
Log in to a Google account with the command
```shell
gcloud auth login
```
This should open a page in your browser starting with  `https://accounts.google.com/o/oauth2/...` where you can complete the login.

If successful, the command prompt will show:
>```shell
>You are now logged in as [<account>@gmail.com].
>```

### Configure project ID
Next, you must associate this installation with GCloud project using "Project ID" from [resource-management](https://console.cloud.google.com/cloud-resource-manager):
```shell
gcloud config set project <PROJECT_ID>
```
Sample output:
>```shell
>Updated property [core/project].
>```

### Install additional gcloud CLI tool

As a last step, install the Debian package `google-cloud-sdk-gke-gcloud-auth-plugin` using this Google guide: [Install the gcloud CLI](https://cloud.google.com/sdk/docs/install#deb).

<a href="#heading--create-gke-cluster"><h2 id="heading--create-gke-cluster"> Create a new GKE cluster</h2></a>

This guide will use high-availability zone `europe-west1` and compute engine type `n1-standard-4` in command examples. Make sure to choose the zone and resources that best suit your use-case.

The following command will start three [compute engines](https://cloud.google.com/compute/) on Google Cloud and deploy a K8s cluster (you can imagine the compute engines as three physical servers in clouds):
```shell
gcloud container clusters create --zone europe-west1-c $USER-$RANDOM --cluster-version 1.25 --machine-type n1-standard-4 --num-nodes=3 --no-enable-autoupgrade
```

Next, assign your account as an admin of the newly created K8s cluster:
```shell
kubectl create clusterrolebinding cluster-admin-binding-$USER --clusterrole=cluster-admin --user=$(gcloud config get-value core/account)
```

<a href="#heading--boostrap-juju"><h2 id="heading--boostrap-juju"> Bootstrap Juju on GKE</h2></a>

Bootstrap a new juju controller on the new cluster by running the following commands:

> Note: [This known issue](https://bugs.launchpad.net/juju/+bug/2007575) forces unSNAPed Juju usage to add-k8s credentials on Juju.

```shell
/snap/juju/current/bin/juju add-k8s gke-jun-9 --storage=standard --client
juju bootstrap gke-jun-9
juju add-model welcome-model
```
At this stage, Juju is ready to use GKE. Check the list of currently running K8s pods with:
```shell
kubectl get pods -n welcome-model
```

<a href="#heading--deploy-charms"><h2 id="heading--multipass"> Deploy charms</h2></a>

The following commands deploy PostgreSQL K8s and PgBouncer K8s:
```shell
juju deploy postgresql-k8s --trust
juju deploy pgbouncer-k8s --trust
```

To track the status of the deployment, run
```shell
juju status --watch 1s
```

<a href="#heading--list-clusters-clouds"><h2 id="heading--list-clusters-clouds"> List clusters and clouds</h2></a>

To list GKE clusters and juju clouds, run:
```shell
gcloud container clusters list
```
Sample output:
>```shell
>NAME          LOCATION        MASTER_VERSION   MASTER_IP      MACHINE_TYPE   NODE_VERSION     >NUM_NODES  STATUS
>mykola-18187  europe-west1-c  1.25.9-gke.2300  31.210.22.127  n1-standard-4  1.25.9-gke.2300  3          >RUNNING
>taurus-7485   europe-west1-c  1.25.9-gke.2300  142.142.21.25  n1-standard-4  1.25.9-gke.2300  3          >RUNNING
>```
Juju can handle multiple clouds simultaneously. To see a list of clouds with registered credentials on Juju, run:
```shell
juju clouds
```
Sample output:
>```shell
>Clouds available on the controller:
>Cloud      Regions  Default       Type
>gke-jun-9  1        europe-west1  k8s  
>
>Clouds available on the client:
>Cloud           Regions  Default       Type  Credentials  Source    Description
>gke-jun-9       1        europe-west1  k8s   1            local     A Kubernetes Cluster
>localhost       1        localhost     lxd   1            built-in  LXD Container Hypervisor
>microk8s        0                      k8s   1            built-in  A local Kubernetes context
>```

<a href="#heading--clean-up"><h2 id="heading--clean-up"> Clean up</h2></a>

[note type="caution"]
**Warning**: Always clean GKE resources that are no longer necessary -  they could be costly!
[/note]
To clean GKE clusters and juju clouds, use:
```shell
juju destroy-controller gke-jun-9-europe-west1 --yes --destroy-all-models --destroy-storage --force
juju remove-cloud gke-jun-9

gcloud container clusters list
gcloud container clusters delete <cluster_name> --zone europe-west1-c
```
Revoke the GCloud user credentials:
```shell
gcloud auth revoke your_account@gmail.com
```
You should see a confirmation output:
>```shell
>Revoked credentials:
 >- your_account@gmail.com
>```