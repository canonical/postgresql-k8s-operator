# Deploy on multiple availability zones (AZ) 

During the deployment to Kubernetes, it is important to spread all the
database copies (K8s pods/Juju units) to different hardware servers,
or even better, to the different cloud [availability zones](https://en.wikipedia.org/wiki/Availability_zone) (AZ). This will guarantee no shared service-critical components across the DB cluster (eliminate the case with all eggs in the same basket).

This guide will take you through deploying a PostgreSQL cluster on GKE using 3 available zones. All pods will be set up to sit in their dedicated zones only, which effectively guarantees database copy survival across all available AZs.

## Prerequisites

* A physical or virtual machine running Ubuntu 22.04+
* Juju 3 (`3.6+` is recommended)
  * See: [How to install Juju](https://documentation.ubuntu.com/juju/3.6/howto/manage-juju/#install-juju)
* A cloud service that supports and provides availability zones concepts such as the K8s label `topology.kubernetes.io/zone`. 

```{note}
Multi-availability zones are enabled by default on EC2/GCE and supported by LXD and MicroCloud.
```

## Set up Kubernetes on Google Cloud

Let's deploy a [PostgreSQL Cluster on GKE (us-east4)](/how-to/deploy/gke) using all 3 zones there (`us-east4-a`, `us-east4-b`, `us-east4-c`) and make sure all pods always sits in the dedicated zones only.

```{caution}
Creating the following GKE resources may cost you money - be sure to monitor your GCloud costs.
```

Log into Google Cloud and bootstrap nine nodes of managed K8s on GCloud:
```text
gcloud auth login
gcloud container clusters create --zone us-east4 $USER-$RANDOM --cluster-version 1.29.8 --machine-type n1-standard-4 --num-nodes=2 --total-max-nodes=2 --no-enable-autoupgrade

kubectl config get-contexts
/snap/juju/current/bin/juju add-k8s gke --client --context-name=$(kubectl config get-contexts | grep gke | awk '{print $2}')
juju bootstrap gke gke
juju add-model mymodel
```

Each node is equally grouped across availability zones (three K8s nodes in each AZ). You can check them with the following `kubectl` commands:

```text
kubectl get nodes
```

```text
> NAME                                          STATUS   ROLES    AGE     VERSION
> gke-default-pool-5c034921-fmtj   Ready    <none>   3h56m   v1.29.8-gke.1278000          
> gke-default-pool-5c034921-t1sx   Ready    <none>   3h56m   v1.29.8-gke.1278000          
> gke-default-pool-5c034921-zkzm   Ready    <none>   3h56m   v1.29.8-gke.1278000          
> gke-default-pool-b33634ac-l0c9   Ready    <none>   3h56m   v1.29.8-gke.1278000          
> gke-default-pool-b33634ac-phjx   Ready    <none>   3h56m   v1.29.8-gke.1278000          
> gke-default-pool-b33634ac-w2jv   Ready    <none>   3h56m   v1.29.8-gke.1278000          
> gke-default-pool-d196956a-0zfc   Ready    <none>   3h56m   v1.29.8-gke.1278000          
> gke-default-pool-d196956a-591j   Ready    <none>   3h56m   v1.29.8-gke.1278000
> gke-default-pool-d196956a-zm6h   Ready    <none>   3h56m   v1.29.8-gke.1278000
```

```text
kubectl get nodes --show-labels | awk 'NR == 1 {next} {print $1,$2,$6}' | awk -F "[ /]" '{print $1" \t"$NF" \t"$2}'
```
```text
gke-default-pool-5c034921-fmtj     zone=us-east4-b         Ready
gke-default-pool-5c034921-t1sx     zone=us-east4-b         Ready
gke-default-pool-5c034921-zkzm     zone=us-east4-b         Ready
gke-default-pool-b33634ac-l0c9     zone=us-east4-c         Ready
gke-default-pool-b33634ac-phjx     zone=us-east4-c         Ready
gke-default-pool-b33634ac-w2jv     zone=us-east4-c         Ready
gke-default-pool-d196956a-0zfc     zone=us-east4-a         Ready
gke-default-pool-d196956a-591j     zone=us-east4-a         Ready
gke-default-pool-d196956a-zm6h     zone=us-east4-a         Ready
```
## Deploy PostgreSQL with anti-affinity rules

Juju provides the support for affinity/anti-affinity rules using **constraints**. Read more about it in this [forum post](https://discourse.charmhub.io/t/pod-priority-and-affinity-in-juju-charms/4091).

The command below demonstrates how to deploy Charmed PostgreSQL K8s with Juju constraints that create a pod anti-affinity rule:

```text
export MYAPP="mydatabase" ; \
juju deploy postgresql-k8s --channel 16/edge  ${MYAPP} --trust -n 3 \
 --constraints="tags=anti-pod.app.kubernetes.io/name=${MYAPP},anti-pod.topology-key=topology.kubernetes.io/zone"
```

This will effectively create a K8s pod anti-affinity rule. Check with the following command:
```text
kubectl get pod mydatabase-0 -o yaml -n mymodel
```
```yaml
...
spec:
  affinity:
    podAntiAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
      - labelSelector:
          matchExpressions:
          - key: app.kubernetes.io/name
            operator: In
            values:
            - mydatabase
        topologyKey: topology.kubernetes.io/zone
...
```
This example instructs the K8s scheduler to run K8s pods that match the Juju application name `app.kubernetes.io/name: my database` label on K8s nodes that have different values of `topology.kubernetes.io/zone` label. In other words, we asked to run all PostgreSQL instances in different Availability Zones, which is the common recommendation for any database deployment.

The selector and affinity/anti-affinity rules are extremely flexible and often require cloud-specific fine-tuning for your specific needs. The rule of thumb is to always check K8s node labels actually available in your environment and choose the appropriate label on your infrastructure. 

The example below shows all available labels on GKE. You may want to run database instances on different virtual machines (`kubernetes.io/hostname` label) or in different Availability Zones (`topology.kubernetes.io/zone` label) :
```text
kubectl get node gke-default-pool-b33634ac-l0c9 -o yaml
```
```yaml
...
  labels:
    beta.kubernetes.io/arch: amd64
    beta.kubernetes.io/instance-type: n1-standard-4
    beta.kubernetes.io/os: linux
    cloud.google.com/gke-boot-disk: pd-balanced
    cloud.google.com/gke-container-runtime: containerd
    cloud.google.com/gke-cpu-scaling-level: "4"
    cloud.google.com/gke-logging-variant: DEFAULT
    cloud.google.com/gke-max-pods-per-node: "110"
    cloud.google.com/gke-nodepool: default-pool
    cloud.google.com/gke-os-distribution: cos
    cloud.google.com/gke-provisioning: standard
    cloud.google.com/gke-stack-type: IPV4
    cloud.google.com/machine-family: n1
    cloud.google.com/private-node: "false"
    failure-domain.beta.kubernetes.io/region: us-east4
    failure-domain.beta.kubernetes.io/zone: us-east4-c
    kubernetes.io/arch: amd64
    kubernetes.io/hostname: gke-default-pool-b33634ac-l0c9
    kubernetes.io/os: linux
    node.kubernetes.io/instance-type: n1-standard-4
    topology.gke.io/zone: us-east4-c
    topology.kubernetes.io/region: us-east4
    topology.kubernetes.io/zone: us-east4-c
...
``` 

After a successful deployment, `juju status` will show an active application:
```text
Model    Controller  Cloud/Region  Version  SLA          Timestamp
mymodel  gke         gke/us-east4  3.5.3    unsupported  22:02:32+02:00

App         Version  Status  Scale  Charm           Channel    Rev  Address         Exposed  Message
mydatabase  14.13    active      3  postgresql-k8s  16/edge    615  34.118.235.169  no       

Unit           Workload  Agent  Address    Ports  Message
mydatabase/0   active    idle   10.80.5.9         
mydatabase/1*  active    idle   10.80.6.7         Primary
mydatabase/2   active    idle   10.80.1.6         
```

and each pod will sit in the separate AZ out of the box:
```text
kubectl get pods -n mymodel -o wide
```
```none
> NAME             READY   STATUS    RESTARTS   AGE   IP          NODE
> mydatabase-0     2/2     Running   0          16m   10.80.5.7   gke-default-pool-b33634ac-l0c9  ... # us-east4-c
> mydatabase-1     2/2     Running   0          24m   10.80.6.7   gke-default-pool-d196956a-0zfc  ... # us-east4-a
> mydatabase-2     2/2     Running   0          24m   10.80.0.3   gke-default-pool-5c034921-zkzm  ... # us-east4-b
> ...
```
### Simulation: A node gets drained
Let's drain a node and make sure the rescheduled pod will stay the same AZ:
```text
kubectl drain  --ignore-daemonsets --delete-emptydir-data  gke-default-pool-b33634ac-l0c9
```
```text
> node/gke-default-pool-b33634ac-l0c9 cordoned                                  
> ...
> evicting pod mymodel/mydatabase-0                       
> ...     
> pod/mydatabase-0 evicted                                
> node/gke-default-pool-b33634ac-l0c9 drained
```

As we can see the newly rescheduled pod landed the new node in the same AZ `us-east4-c`:
```text
kubectl get pods -n mymodel -o wide

> NAME             READY   STATUS    RESTARTS   AGE   IP          NODE                                       
> mydatabase-0     2/2     Running   0           1m   10.80.5.7   gke-default-pool-b33634ac-phjx  ... # us-east4-c
> mydatabase-1     2/2     Running   0          35m   10.80.6.7   gke-default-pool-d196956a-0zfc  ... # us-east4-a
> mydatabase-2     2/2     Running   0          35m   10.80.0.3   gke-default-pool-5c034921-zkzm  ... # us-east4-b
> ...
```

### Simulation: All nodes get cordoned

In case we lose (cordon) all nodes in AZ, the pod will stay pending as K8s scheduler cannot find the proper node.
Let's simulate it:
```text
kubectl drain  --ignore-daemonsets --delete-emptydir-data  gke-default-pool-b33634ac-phjx
kubectl drain  --ignore-daemonsets --delete-emptydir-data  gke-default-pool-b33634ac-w2jv

kubectl get nodes --show-labels | awk 'NR == 1 {next} {print $1,$2,$6}' | awk -F "[ /]" '{print $1" \t"$NF" \t"$2}'
> gke-default-pool-5c034921-fmtj     zone=us-east4-b         Ready
> gke-default-pool-5c034921-t1sx     zone=us-east4-b         Ready
> gke-default-pool-5c034921-zkzm     zone=us-east4-b         Ready
> gke-default-pool-b33634ac-l0c9     zone=us-east4-c         Ready,SchedulingDisabled
> gke-default-pool-b33634ac-phjx     zone=us-east4-c         Ready,SchedulingDisabled
> gke-default-pool-b33634ac-w2jv     zone=us-east4-c         Ready,SchedulingDisabled
> gke-default-pool-d196956a-0zfc     zone=us-east4-a         Ready
> gke-default-pool-d196956a-591j     zone=us-east4-a         Ready
> gke-default-pool-d196956a-zm6h     zone=us-east4-a         Ready
```

```text
kubectl get pods -n mymodel

> NAME                       READY   STATUS    RESTARTS   AGE
> mydatabase-0               0/2     Pending   0          2m9s # Pending!!!
> mydatabase-1               2/2     Running   0          96m
> mydatabase-2               2/2     Running   0          51m

kubectl describe pod mydatabase-0 -n mymodel  | tail -10

> Events:
>   Type     Reason             Age    From                Message
>   ----     ------             ----   ----                -------
>   Warning  FailedScheduling   3m32s  default-scheduler   0/9 nodes are available: 3 node(s) were unschedulable, 6 node(s) had volume node affinity conflict. preemption: 0/9 nodes are available: 9 Preemption is not helpful for scheduling.
>   Warning  FailedScheduling   3m30s  default-scheduler   0/9 nodes are available: 3 node(s) were unschedulable, 6 node(s) had volume node affinity conflict. preemption: 0/9 nodes are available: 9 Preemption is not helpful for scheduling.
>   Warning  FailedScheduling   3m27s  default-scheduler   0/9 nodes are available: 3 node(s) were unschedulable, 6 node(s) had volume node affinity conflict. preemption: 0/9 nodes are available: 9 Preemption is not helpful for scheduling.
>   Normal   NotTriggerScaleUp  3m33s  cluster-autoscaler  pod didn't trigger scale-up:
```

The `juju status` output will indicate this problem as well:
```text
Model    Controller  Cloud/Region  Version  SLA          Timestamp
mymodel  gke         gke/us-east4  3.5.3    unsupported  22:31:00+02:00

App         Version  Status   Scale  Charm           Channel    Rev  Address         Exposed  Message
mydatabase  14.13    waiting    2/3  postgresql-k8s  16/edge    615  34.118.235.169  no       installing agent

Unit           Workload  Agent  Address    Ports  Message
mydatabase/0   unknown   lost                     agent lost, see 'juju show-status-log mydatabase/0'
mydatabase/1*  active    idle   10.80.6.7         Primary
mydatabase/2   active    idle   10.80.1.6         
```

Let's uncordon all nodes to keep the house clean:
```text
kubectl uncordon gke-default-pool-b33634ac-l0c9
kubectl uncordon gke-default-pool-b33634ac-phjx
kubectl uncordon gke-default-pool-b33634ac-w2jv
```

The K8s scheduler will return the pod back to AZ `us-east4-c` and Juju will automatically rejoin the database unit back to the cluster:
```text
Model    Controller  Cloud/Region  Version  SLA          Timestamp
mymodel  gke         gke/us-east4  3.5.3    unsupported  22:38:23+02:00

App         Version  Status  Scale  Charm           Channel    Rev  Address         Exposed  Message
mydatabase  14.13    active      3  postgresql-k8s  16/edge    615  34.118.235.169  no       

Unit           Workload  Agent  Address     Ports  Message
mydatabase/0   active    idle   10.80.5.10         
mydatabase/1*  active    idle   10.80.6.7          Primary
mydatabase/2   active    idle   10.80.1.6   
```

At this point we can relax and enjoy the protection from Cloud Availability zones!

To survive a complete cloud outage, we recommend setting up [cluster-cluster asynchronous replication](/how-to/cross-regional-async-replication/set-up-clusters).


## Remove GKE setup

```{caution}
Do not forget to remove your GKE test setup - it can be costly!
```

```text
gcloud container clusters list
gcloud container clusters delete <gke_name> --location <gke_location>

juju unregister gke --no-prompt
juju remove-cloud gke
```

## Additional resources
Below you will find specific information about  AZs on specific clouds and more about node selection on Kubernetes.

### Cloud-specific details about multiple availability zones
 * [General Kubernetes](https://kubernetes.io/docs/setup/best-practices/multiple-zones/)
 * [AWS/EKS](https://aws.amazon.com/rds/features/multi-az/)
 * [GCloud/GKE](https://cloud.google.com/kubernetes-engine/multi-cloud/docs/azure/how-to/create-cluster)
 * [Azure/AKS](https://learn.microsoft.com/en-us/azure/aks/availability-zones)

### Kubernetes strategies to choose hardware nodes
 * [Node selector](https://kubernetes.io/docs/tasks/configure-pod-container/assign-pods-nodes/)
 * [Affinity/anti-affinity](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#affinity-and-anti-affinity)
 * [Taint and toleration](https://kubernetes.io/docs/concepts/scheduling-eviction/taint-and-toleration/)

