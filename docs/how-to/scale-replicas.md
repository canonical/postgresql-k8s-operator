# How to scale units

Replication in PostgreSQL is the process of creating copies of the stored data. This provides redundancy, which means the application can provide self-healing capabilities in case one replica fails. In this context, each replica is equivalent one juju unit.

This guide will show you how to establish and change the amount of juju units used to replicate your data. 

## Deploy PostgreSQL with replicas

To deploy PostgreSQL with multiple replicas, specify the number of desired units with the `-n` option:

```text
juju deploy postgresql-k8s --channel 16/stable -n <number_of_replicas> --trust
```

### Primary vs. leader unit

The [PostgreSQL primary server](https://www.postgresql.org/docs/current/runtime-config-replication.html#RUNTIME-CONFIG-REPLICATION-PRIMARY) unit may or may not be the same as the [juju leader unit](https://juju.is/docs/juju/leader). 

The juju leader unit is the represented in `juju status` by an asterisk (*) next to its name. 

To retrieve the juju unit that corresponds to the PostgreSQL primary, use the action `get-primary` on any of the units running ` postgresql-k8s`:

```text
juju run postgresql-k8s/leader get-primary
```

Similarly, the primary replica is displayed as a status message in `juju status`. However, one should note that this hook gets called on regular time intervals and the primary may be outdated if the status hook has not been called recently. 

````{note}
**We highly suggest configuring the `update-status` hook to run frequently.** In addition to reporting the primary, secondaries, and other statuses, the [status hook](https://documentation.ubuntu.com/juju/3.6/reference/hook/#update-status) performs self-healing in the case of a network cut. 

To change the frequency of the `update-status` hook, run

```text
juju model-config update-status-hook-interval=<time(s/m/h)>
```

This hook executes a read query to PostgreSQL. On a production level server, this should be configured to occur at a frequency that doesn't overload the server with read requests. Similarly, the hook should not be configured at too quick of a frequency, as this can delay other hooks from running.
````

## Scale replicas on an existing application

Both scaling up and scaling down operations are performed using `juju scale-application`:

```text
juju scale-application postgresql-k8s <desired_num_of_units>
```

```{important}
Scaling down to zero units will destroy your data!
```

