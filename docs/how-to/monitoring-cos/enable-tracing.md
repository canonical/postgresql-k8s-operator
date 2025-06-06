# Enable tracing

This guide contains the steps to enable tracing with [Grafana Tempo](https://grafana.com/docs/tempo/latest/) for your PostgreSQL K8s application. 

```{caution}
This is feature is in development. It is **not recommended** for production environments. 
```

## Prerequisites

Enabling tracing with Tempo requires that you:

- Have deployed a Charmed PostgreSQL K8s revision 292+
  - See [](/how-to/deploy/index)
- Have deployed a 'cos-lite' bundle from the `latest/edge` track in a Kubernetes environment
  - See [Getting started on MicroK8s](https://charmhub.io/topics/canonical-observability-stack/tutorials/install-microk8s)

## Deploy Tempo

First, switch to the Kubernetes controller where the COS model is deployed:

```text
juju switch <k8s_controller_name>:<cos_model_name>
```
Then, deploy the dependencies of Tempo following [this tutorial](https://discourse.charmhub.io/t/tutorial-deploy-tempo-ha-on-top-of-cos-lite/15489). In particular, we would want to:
- Deploy the minio charm
- Deploy the s3 integrator charm
- Add a bucket in minio using a python script
- Configure s3 integrator with the minio credentials

Finally, deploy and integratre with Tempo HA in [a monolithic setup](https://discourse.charmhub.io/t/tutorial-deploy-tempo-ha-on-top-of-cos-lite/15489).

## Offer interfaces

Next, offer interfaces for cross-model integrations from the model where Charmed PostgreSQL K8s is deployed.

To offer the Tempo integration, run

```text
juju offer <tempo_coordinator_k8s_application_name>:tracing
```

Then, switch to the Charmed PostgreSQL K8s model, find the offers, and integrate (relate) with them:

```text
juju switch <k8s_controller_name>:<postgresql_k8s_model_name>

juju find-offers <k8s_controller_name>:
```
```{tip}
Don't miss the "`:`" in the command above.
```

Below is a sample output where `k8s` is the K8s controller name and `cos` is the model where `cos-lite` and `tempo-k8s` are deployed:

```text
Store  URL                            Access  Interfaces
k8s    admin/cos.tempo                admin   tracing:tracing
```

Next, consume this offer so that it is reachable from the current model:

```text
juju consume k8s:admin/cos.tempo
```

## Consume interfaces

First, deploy [Grafana Agent K8s](https://charmhub.io/grafana-agent-k8s) from the `latest/edge` channel:

```text
juju deploy grafana-agent-k8s --channel latest-edge 
```

Then, integrate Grafana Agent K8s with the consumed interface from the previous section:

```text
juju integrate grafana-agent-k8s:tracing tempo:tracing
```

Finally, integrate Charmed PostgreSQL K8s with Grafana Agent K8s:

```text
juju integrate postgresql-k8s:tracing grafana-agent-k8s:tracing-provider
```

Wait until the model settles. The following is an example of the `juju status --relations` on the Charmed PostgreSQL model:

```text  
Model     Controller  Cloud/Region        Version  SLA          Timestamp
database  k8s         microk8s/localhost  3.5.4    unsupported  16:52:21Z

SAAS   Status  Store       URL
tempo  active  k8s         admin/cos.tempo

App                Version  Status  Scale  Charm              Channel      Rev  Address         Exposed  Message
grafana-agent-k8s  0.40.4   active      1  grafana-agent-k8s  latest/edge   93  10.152.183.226  no       grafana-dashboards-provider: off, logging-consumer: off, send-remote-write: off
postgresql-k8s     14.13    active      1  postgresql-k8s                    0  10.152.183.96   no       

Unit                  Workload  Agent  Address       Ports  Message
grafana-agent-k8s/0*  active    idle   10.1.241.195         grafana-dashboards-provider: off, logging-consumer: off, send-remote-write: off
postgresql-k8s/0*     active    idle   10.1.241.197         Primary

Integration provider                Requirer                       Interface              Type     Message
grafana-agent-k8s:peers             grafana-agent-k8s:peers        grafana_agent_replica  peer     
grafana-agent-k8s:tracing-provider  postgresql-k8s:tracing         tracing                regular  
postgresql-k8s:database-peers       postgresql-k8s:database-peers  postgresql_peers       peer     
postgresql-k8s:restart              postgresql-k8s:restart         rolling_op             peer     
postgresql-k8s:upgrade              postgresql-k8s:upgrade         upgrade                peer     
tempo:tracing                       grafana-agent-k8s:tracing      tracing                regular  

```

```{note}
All traces are exported to Tempo using HTTP. Support for sending traces via HTTPS is an upcoming feature.
```

## View traces

After this is complete, the Tempo traces will be accessible from Grafana under the `Explore` section with `tempo-k8s` as the data source. You will be able to select `postgresql-k8s` as the `Service Name` under the `Search` tab to view traces belonging to Charmed PostgreSQL K8s.

Below is a screenshot demonstrating a Charmed PostgreSQL K8s trace:

![Example PostgreSQL trace with Grafana Tempo|690x382](trace-grafana-tempo.jpeg)

Feel free to read through the [Tempo HA documentation](https://discourse.charmhub.io/t/charmed-tempo-ha/15531) at your leisure to explore its deployment and its integrations.

