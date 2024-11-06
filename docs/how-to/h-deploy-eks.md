[note]
**Note**: All commands are written for `juju >= v.3.0`

If you are using an earlier version,  check the [Juju 3.0 Release Notes](https://juju.is/docs/juju/roadmap#heading--juju-3-0-0---22-oct-2022).
[/note]

# How to deploy on EKS

[Amazon Elastic Kubernetes Service](https://aws.amazon.com/eks/) (EKS) is a popular, fully automated Kubernetes service. To access the EKS Web interface, go to [console.aws.amazon.com/eks/home](https://console.aws.amazon.com/eks/home).

## Summary
* [Install EKS and Juju tooling](#heading--install-eks-juju)
* [Create a new EKS cluster](#heading--create-eks-cluster)
* [Bootstrap Juju on EKS](#heading--boostrap-juju)
* [Deploy charms](#heading--deploy-charms)
* [Display deployment information](#heading--display-information)
* [Clean up](#heading--clean-up)

---

<a href="#heading--install-eks-juju"><h2 id="heading--install-eks-juju"> Install EKS and Juju tooling</h2></a>

Install [Juju](https://juju.is/docs/juju/install-juju) and the [`kubectl` CLI tools](https://kubernetes.io/docs/tasks/tools/) via snap:
```shell
sudo snap install juju
sudo snap install kubectl --classic
```
Follow the installation guides for:
* [eksctl](https://eksctl.io/installation/) - the Amazon EKS CLI
* [AWs CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) - the Amazon Web Services CLI

To check they are all correctly installed, you can run the commands demonstrated below with sample outputs:

```shell
> juju version
3.1.7-ubuntu-amd64

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
```shell
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

<a href="#heading--create-eks-cluster"><h2 id="heading--create-eks-cluster"> Create a new EKS cluster</h2></a>

Export the deployment name for further use:
```shell
export JUJU_NAME=eks-$USER-$RANDOM
```

This following examples in this guide will use the location `eu-west-3` and K8s `v.1.27` - feel free to change this for your own deployment.

Sample `cluster.yaml`:

```shell
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
```shell
eksctl create cluster -f cluster.yaml
```
Sample output:
```shell
...
2023-10-12 11:13:58 [ℹ]  using region eu-west-3
2023-10-12 11:13:59 [ℹ]  using Kubernetes version 1.27
...
2023-10-12 11:40:00 [✔]  EKS cluster "eks-taurus-27506" in "eu-west-3" region is ready
```

<a href="#heading--boostrap-juju"><h2 id="heading--boostrap-juju"> Bootstrap Juju on EKS</h2></a>

[note type="caution"]
There is a known bug for `juju v.3.1` users: 
bugs.launchpad.net/juju/+bug/2007848
[/note]

Add Juju k8s clouds:
```shell
juju add-k8s $JUJU_NAME
```
Bootstrap Juju controller:
```shell
juju bootstrap $JUJU_NAME
```
Create a new Juju model (k8s namespace)
```shell
juju add-model welcome
```
[Optional] Increase DEBUG level if you are troubleshooting charms 
```shell
juju model-config logging-config='<root>=INFO;unit=DEBUG'
```

<a href="#heading--deploy-charms"><h2 id="heading--deploy-charms"> Deploy charms</h2></a>

The following command deploys PostgreSQL K8s, PgBouncer K8s, a TLS certificate provider, and the PostgreSQL Test app:

```shell
juju deploy postgresql-k8s-bundle --channel 14/edge --trust
juju deploy postgresql-test-app
```
We then integrate the test app with the `postgresql-k8s` app and check the status:
```shell
juju integrate postgresql-test-app:first-database postgresql-k8s
juju status --watch 1s
```
<a href="#heading--display-information"><h3 id="heading--display-information"> Display deployment information</h3></a>

Display information about the current deployments with the following commands:
```shell
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

<a href="#heading--clean-up"><h2 id="heading--clean-up"> Clean up</h2></a>

[note type="caution"]
Always clean EKS resources that are no longer necessary -  they could be costly!
[/note]

To clean the EKS cluster, resources and juju cloud, run the following commands:

```shell
juju destroy-controller $JUJU_NAME --yes --destroy-all-models --destroy-storage --force
juju remove-cloud $JUJU_NAME
```
List all services and then delete those that have an associated EXTERNAL-IP value (e.g. load balancers):
```shell
kubectl get svc --all-namespaces
kubectl delete svc <service-name> 
```
Next, delete the EKS cluster  (source: [Deleting an Amazon EKS cluster]((https://docs.aws.amazon.com/eks/latest/userguide/delete-cluster.html) )) 
```shell
eksctl get cluster -A
eksctl delete cluster <cluster_name> --region eu-west-3 --force --disable-nodegroup-eviction
```
Finally, remove AWS CLI user credentials (to avoid forgetting and leaking):
```shell
rm -f ~/.aws/credentials
```