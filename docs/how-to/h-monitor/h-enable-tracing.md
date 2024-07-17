[note]
**Note**: All commands are written for `juju >= v3.1`

If you're using `juju 2.9`, check the [`juju 3.0` Release Notes](https://juju.is/docs/juju/roadmap#heading--juju-3-0-0---22-oct-2022).
[/note]

# Enable tracing
This guide contains the steps to enable tracing with [Grafana Tempo](https://grafana.com/docs/tempo/latest/) for your PostgreSQL K8s application. 

To summarize:
* [Deploy the Tempo charm in a COS K8s environment](#heading--deploy)
* [Integrate it with the COS charms](#heading--integrate)
* [Offer interfaces for cross-model integrations](#heading--offer)
* [View PostgreSQL K8s traces on Grafana](#heading--view)


[note type="caution"]
**Warning:** This is feature is in development. It is **not recommended** for production environments. 

This feature is available for Charmed PostgreSQL K8s revision 292+ only.
[/note]

## Prerequisites
Enabling tracing with Tempo requires that you:
- Have deployed a Charmed PostgreSQL K8s application
  - See [How to scale units](https://discourse.charmhub.io/t/charmed-postgresql-k8s-how-to-scale-units/9592)
- Have deployed a 'cos-lite' bundle from the `latest/edge` track in a Kubernetes environment
  - See [Getting started on MicroK8s](https://charmhub.io/topics/canonical-observability-stack/tutorials/install-microk8s)

---
<a href="#heading--deploy"><h2 id="heading--deploy"> Deploy Tempo </h2></a>

First, switch to the Kubernetes controller where the COS model is deployed:

```shell
juju switch <k8s_controller_name>:<cos_model_name>
```
Then, deploy the [`tempo-k8s`](https://charmhub.io/tempo-k8s) charm:
```shell
juju deploy -n 1 tempo-k8s --channel latest/edge
```

<a href="#heading--integrate"><h2 id="heading--integrate"> Integrate with the COS charms </h2></a>

Integrate `tempo-k8s` with the COS charms as follows:

```shell
juju integrate tempo-k8s:grafana-dashboard grafana:grafana-dashboard
juju integrate tempo-k8s:grafana-source grafana:grafana-source
juju integrate tempo-k8s:ingress traefik:traefik
juju integrate tempo-k8s:metrics-endpoint prometheus:metrics-endpoint
juju integrate tempo-k8s:logging loki:logging
```
If you would like to instrument traces from the COS charms as well, create the following integrations:
```shell
juju integrate tempo-k8s:tracing alertmanager:tracing
juju integrate tempo-k8s:tracing catalogue:tracing
juju integrate tempo-k8s:tracing grafana:tracing
juju integrate tempo-k8s:tracing loki:tracing
juju integrate tempo-k8s:tracing prometheus:tracing
juju integrate tempo-k8s:tracing traefik:tracing
```

<a href="#heading--offer"><h2 id="heading--offer"> Offer interfaces </h2></a>

Next, offer interfaces for cross-model integrations from the model where Charmed PostgreSQL K8s is deployed.

To offer the Tempo integration, run

```shell
juju offer tempo-k8s:tracing
```

Then, switch to the Charmed PostgreSQL K8s model, find the offers, and integrate (relate) with them:

```shell
juju switch <k8s_controller_name>:<postgresql_k8s_model_name>

juju find-offers <k8s_controller_name>:
```
> :exclamation: Do not miss the "`:`" in the command above.

Below is a sample output where `k8s` is the K8s controller name and `cos` is the model where `cos-lite` and `tempo-k8s` are deployed:

```shell
Store  URL                            Access  Interfaces
k8s    admin/cos.tempo-k8s            admin   tracing:tracing
```

Next, consume this offer so that it is reachable from the current model:

```shell
juju consume k8s:admin/cos.tempo-k8s
```

Relate Charmed PostgreSQL with the above consumed interface:

```shell
juju integrate postgresql-k8s:tracing tempo-k8s:tracing
```

Wait until the model settles. The following is an example of the `juju status --relations` on the Charmed PostgreSQL model:

```shell

Model     Controller  Cloud/Region        Version  SLA          Timestamp
database  k8s         microk8s/localhost  3.4.3    unsupported  20:33:10Z

SAAS       Status  Store  URL
tempo-k8s  active  k8s    admin/cos.tempo-k8s

App             Version  Status  Scale  Charm           Channel  Rev  Address         Exposed  Message
postgresql-k8s  14.11    active      1  postgresql-k8s           292  10.152.183.249  no       

Unit               Workload  Agent  Address       Ports  Message
postgresql-k8s/0*  active    idle   10.1.241.252         Primary

Integration provider           Requirer                       Interface         Type     Message
postgresql-k8s:database-peers  postgresql-k8s:database-peers  postgresql_peers  peer     
postgresql-k8s:restart         postgresql-k8s:restart         rolling_op        peer     
postgresql-k8s:upgrade         postgresql-k8s:upgrade         upgrade           peer     
tempo-k8s:tracing              postgresql-k8s:tracing         tracing           regular  

```

[note]
**Note:** All traces are exported to Tempo using HTTP. Support for sending traces via HTTPS is an upcoming feature.
[/note]


<a href="#heading--view"><h2 id="heading--view"> View traces </h2></a>

After this is complete, the Tempo traces will be accessible from Grafana under the `Explore` section with `tempo-k8s` as the data source. You will be able to select `postgresql-k8s` as the `Service Name` under the `Search` tab to view traces belonging to Charmed PostgreSQL K8s.

Below is a screenshot demonstrating a Charmed PostgreSQL K8s trace:

![Example PostgreSQL K8s trace with Grafana Tempo|690x382](upload://vweRlTS48WILFxvSdvFVFFeuIe5.jpeg)

Feel free to read through the [Tempo documentation](https://discourse.charmhub.io/t/tempo-k8s-docs-index/14005) at your leisure to explore its deployment and its integrations.