# How to deploy on GKE

[Google Kubernetes Engine](https://cloud.google.com/kubernetes-engine?hl=en) (GKE) is a highly scalable and fully automated Kubernetes service. To access the GKE Web interface, go to [console.cloud.google.com/compute](https://console.cloud.google.com/compute).

This guide will walk you through setting up a cluster and deploying PostgreSQL K8s on GKE.

## Prerequisites

This guide assumes you have:

* A physical or virtual machine running Ubuntu 22.04+
* Juju 3 (`3.6+` is recommended)
  * See: [How to install Juju](https://documentation.ubuntu.com/juju/3.6/howto/manage-juju/#install-juju)

## Install GKE tooling

Install `kubectl`, and Google Cloud command-line tools via snap:

```text
sudo snap install kubectl --classic
sudo snap install google-cloud-cli --classic
```

### Authenticate

Log in to a Google account with the command

```text
gcloud auth login
```
This should open a page in your browser starting with  `https://accounts.google.com/o/oauth2/...` where you can complete the login.

If successful, the command prompt will show:
>```text
>You are now logged in as [<account>@gmail.com].
>```

### Configure project ID

Next, you must associate this installation with GCloud project using "Project ID" from [resource-management](https://console.cloud.google.com/cloud-resource-manager):
```text
gcloud config set project <PROJECT_ID>
```
Sample output:
>```text
>Updated property [core/project].
>```

### Install additional gcloud CLI tool

As a last step, install the Debian package `google-cloud-sdk-gke-gcloud-auth-plugin` using this Google guide: [Install the gcloud CLI](https://cloud.google.com/sdk/docs/install#deb).

## Create a new GKE cluster

This guide will use high-availability zone `europe-west1` and compute engine type `n1-standard-4` in command examples. Make sure to choose the zone and resources that best suit your use-case.

The following command will start three [compute engines](https://cloud.google.com/compute/) on Google Cloud and deploy a K8s cluster (you can imagine the compute engines as three physical servers in clouds):

```text
gcloud container clusters create --zone europe-west1-c $USER-$RANDOM --cluster-version 1.25 --machine-type n1-standard-4 --num-nodes=3 --no-enable-autoupgrade
```

Next, assign your account as an admin of the newly created K8s cluster:

```text
kubectl create clusterrolebinding cluster-admin-binding-$USER --clusterrole=cluster-admin --user=$(gcloud config get-value core/account)
```

## Bootstrap Juju on GKE

Bootstrap a new juju controller on the new cluster by running the following commands:

```text
/snap/juju/current/bin/juju add-k8s gke-jun-9 --storage=standard --client
juju bootstrap gke-jun-9
juju add-model welcome-model
```

```{note}
[This known issue](https://bugs.launchpad.net/juju/+bug/2007575) forces non-snap Juju usage to add-k8s credentials on Juju.
```

At this stage, Juju is ready to use GKE. Check the list of currently running K8s pods with:

```text
kubectl get pods -n welcome-model
```

## Deploy charms

The following commands deploy PostgreSQL K8s and PgBouncer K8s:

```text
juju deploy postgresql-k8s --channel 16/edge --trust
juju deploy pgbouncer-k8s --trust
```

To track the status of the deployment, run

```text
juju status --watch 1s
```

## List clusters and clouds

To list GKE clusters and juju clouds, run:

```text
gcloud container clusters list
```

Sample output:

```text
>NAME          LOCATION        MASTER_VERSION   MASTER_IP      MACHINE_TYPE   NODE_VERSION     >NUM_NODES  STATUS
>mykola-18187  europe-west1-c  1.25.9-gke.2300  31.210.22.127  n1-standard-4  1.25.9-gke.2300  3          >RUNNING
>taurus-7485   europe-west1-c  1.25.9-gke.2300  142.142.21.25  n1-standard-4  1.25.9-gke.2300  3          >RUNNING
```

Juju can handle multiple clouds simultaneously. To see a list of clouds with registered credentials on Juju, run:

```text
juju clouds
```

Sample output:

```text
>Clouds available on the controller:
>Cloud      Regions  Default       Type
>gke-jun-9  1        europe-west1  k8s  
>
>Clouds available on the client:
>Cloud           Regions  Default       Type  Credentials  Source    Description
>gke-jun-9       1        europe-west1  k8s   1            local     A Kubernetes Cluster
>localhost       1        localhost     lxd   1            built-in  LXD Container Hypervisor
>microk8s        0                      k8s   1            built-in  A local Kubernetes context
>
```

## Clean up

```{caution}
Always clean GKE resources that are no longer necessary -  they could be costly!
```
To clean GKE clusters and juju clouds, use:

```text
juju destroy-controller gke-jun-9-europe-west1 --yes --destroy-all-models --destroy-storage --force
juju remove-cloud gke-jun-9

gcloud container clusters list
gcloud container clusters delete <cluster_name> --zone europe-west1-c
```

Revoke the GCloud user credentials:

```text
gcloud auth revoke your_account@gmail.com
```

You should see a confirmation output:

```text
>Revoked credentials:
 >- your_account@gmail.com
>
```

