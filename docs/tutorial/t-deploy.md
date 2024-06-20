> [Charmed PostgreSQL K8s Tutorial](/t/9296) >  2. Deploy PostgreSQL

# Deploy Charmed PostgreSQL K8s

In this section, you will deploy a postgresql-k8s application to your juju model and track its status.

## Summary
- [Deploy PostgreSQL](#heading--deploy)
- [Track status](#heading--track-status)
---

<a href="#heading--deploy"><h2 id="heading--deploy"> Deploy PostgreSQL</h2></a>

To deploy Charmed PostgreSQL K8s, run
```shell
juju deploy postgresql-k8s --trust
```

[note]
`--trust` is required because the charm and Patroni need to create some K8s resources.
[/note]

Juju will now fetch Charmed PostgreSQL K8s from [Charmhub](https://charmhub.io/postgresql-k8s?channel=14/stable)  and deploy it to the local MicroK8s. This process can take several minutes depending on how provisioned (RAM, CPU, etc) your machine is. 

<a href="#heading--track-status"><h2 id="heading--track-status"> Track status </h2></a>

You can track the deployment by running:
```shell
juju status --watch 1s
```
This command is useful for checking the real-time information about the state of a charm and the machines hosting it. Check the [`juju status` documentation](https://juju.is/docs/juju/juju-status) for more information about its usage.

When the application is ready, `juju status` will show something similar to the sample output below:
```
Model     Controller  Cloud/Region        Version  SLA          Timestamp
tutorial  charm-dev   microk8s/localhost  2.9.42   unsupported  12:00:43+01:00

App             Version  Status  Scale  Charm           Channel    Rev  Address         Exposed  Message
postgresql-k8s           active      1  postgresql-k8s  14/stable  56   10.152.183.167  no

Unit               Workload  Agent  Address       Ports  Message
postgresql-k8s/0*  active    idle   10.1.188.206
```
You can also watch juju logs with the [`juju debug-log`](https://juju.is/docs/juju/juju-debug-log) command.
More info on logging in the [juju logs documentation](https://juju.is/docs/olm/juju-logs).


**Next step:** [3. Access PostgreSQL](/t/13702)