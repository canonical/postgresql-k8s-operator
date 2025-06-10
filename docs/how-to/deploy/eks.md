# How to deploy on EKS

The [Amazon Elastic Kubernetes Service](https://aws.amazon.com/eks/) (EKS) is a popular, fully automated Kubernetes service. To access the EKS web interface, go to [console.aws.amazon.com/eks/home](https://console.aws.amazon.com/eks/home).

## Prerequisites

This guide assumes you have:

* A physical or virtual machine running Ubuntu 22.04+
* Juju 3 (`3.6+` is recommended)
  * See: [How to install Juju](https://documentation.ubuntu.com/juju/3.6/howto/manage-juju/#install-juju)

## Install EKS tooling

Install the [`kubectl` CLI tools](https://kubernetes.io/docs/tasks/tools/) via snap:
```text
sudo snap install kubectl --classic
```

Follow the installation guides for:
* The [Amazon EKS CLI](https://eksctl.io/installation/)
* The [Amazon Web Services CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)

To check they are all correctly installed, you can run the commands demonstrated below with sample outputs:

```text
> kubectl version --client
Client Version: v1.28.2
Kustomize Version: v5.0.4-0.20230601165947-6ce0bf390ce3

> eksctl info
eksctl version: 0.159.0
kubectl version: v1.28.2

> aws --version
aws-cli/2.13.25 Python/3.11.5 Linux/6.2.0-33-generic exe/x86_64.ubuntu.23 prompt/off
```

### Authenticate

Create an IAM account (or use legacy access keys) and login to AWS:

```text
> aws configure
AWS Access Key ID [None]: SECRET_ACCESS_KEY_ID
AWS Secret Access Key [None]: SECRET_ACCESS_KEY_VALUE
Default region name [None]: eu-west-3
Default output format [None]:

> aws sts get-caller-identity
{
    "UserId": "1234567890",
    "Account": "1234567890",
    "Arn": "arn:aws:iam::1234567890:root"
}
```

## Create a new EKS cluster

Export the deployment name for further use:
```text
export JUJU_NAME=eks-$USER-$RANDOM
```

This following examples in this guide will use the location `eu-west-3` and K8s `v.1.27` - feel free to change this for your own deployment.

Sample `cluster.yaml`:

```text
~$ cat <<-EOF > cluster.yaml
---
apiVersion: eksctl.io/v1alpha5
kind: ClusterConfig

metadata:
    name: ${JUJU_NAME}
    region: eu-west-3
    version: "1.27"
iam:
  withOIDC: true

addons:
- name: aws-ebs-csi-driver
  wellKnownPolicies:
    ebsCSIController: true

nodeGroups:
    - name: ng-1
      minSize: 3
      maxSize: 5
      iam:
        attachPolicyARNs:
        - arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy
        - arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy
        - arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly
        - arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
        - arn:aws:iam::aws:policy/AmazonS3FullAccess
      instancesDistribution:
        maxPrice: 0.15
        instanceTypes: ["m5.xlarge", "m5.2xlarge"] # At least two instance types should be specified
        onDemandBaseCapacity: 0
        onDemandPercentageAboveBaseCapacity: 50
        spotInstancePools: 2
EOF
```
Bootstrap EKS cluster with the following command:
```text
eksctl create cluster -f cluster.yaml
```
Sample output:
```text
...
2023-10-12 11:13:58 [ℹ]  using region eu-west-3
2023-10-12 11:13:59 [ℹ]  using Kubernetes version 1.27
...
2023-10-12 11:40:00 [✔]  EKS cluster "eks-taurus-27506" in "eu-west-3" region is ready
```

## Bootstrap Juju on EKS

```{caution}
There is a known bug for `juju v.3.1` users: 
bugs.launchpad.net/juju/+bug/2007848
```

Add Juju k8s clouds:
```text
juju add-k8s $JUJU_NAME
```
Bootstrap Juju controller:
```text
juju bootstrap $JUJU_NAME
```
Create a new Juju model (k8s namespace)
```text
juju add-model welcome
```
[Optional] Increase DEBUG level if you are troubleshooting charms 
```text
juju model-config logging-config='<root>=INFO;unit=DEBUG'
```

## Deploy charms

The following command deploys PostgreSQL K8s, PgBouncer K8s, a TLS certificate provider, and the PostgreSQL Test app:

```text
juju deploy postgresql-k8s-bundle --channel 14/edge --trust
juju deploy postgresql-test-app
```
We then integrate the test app with the `postgresql-k8s` app and check the status:
```text
juju integrate postgresql-test-app:first-database postgresql-k8s
juju status --watch 1s
```
### Display deployment information

Display information about the current deployments with the following commands:
```text
~$ kubectl cluster-info 
Kubernetes control plane is running at https://AAAAAAAAAAAAAAAAAAAAAAA.gr7.eu-west-3.eks.amazonaws.com
CoreDNS is running at https://AAAAAAAAAAAAAAAAAAAAAAA.gr7.eu-west-3.eks.amazonaws.com/api/v1/namespaces/kube-system/services/kube-dns:dns/proxy

~$ eksctl get cluster -A
NAME			    REGION		EKSCTL   CREATED
eks-taurus-27506	eu-west-3	True

~$ kubectl get node
NAME                                           STATUS   ROLES    AGE   VERSION
ip-192-168-14-61.eu-west-3.compute.internal    Ready    <none>   19m   v1.27.5-eks-43840fb
ip-192-168-51-96.eu-west-3.compute.internal    Ready    <none>   19m   v1.27.5-eks-43840fb
ip-192-168-78-167.eu-west-3.compute.internal   Ready    <none>   19m   v1.27.5-eks-43840fb
```

## Clean up

```{caution}
Always clean EKS resources that are no longer necessary -  they could be costly!
```

To clean the EKS cluster, resources and juju cloud, run the following commands:

```text
juju destroy-controller $JUJU_NAME --yes --destroy-all-models --destroy-storage --force
juju remove-cloud $JUJU_NAME
```

List all services and then delete those that have an associated EXTERNAL-IP value (e.g. load balancers):

```text
kubectl get svc --all-namespaces
kubectl delete svc <service-name> 
```

Next, delete the EKS cluster (source: [Deleting an Amazon EKS cluster](https://docs.aws.amazon.com/eks/latest/userguide/delete-cluster.html))

```text
eksctl get cluster -A
eksctl delete cluster <cluster_name> --region eu-west-3 --force --disable-nodegroup-eviction
```

Finally, remove AWS CLI user credentials (to avoid forgetting and leaking):

```text
rm -f ~/.aws/credentials
```

