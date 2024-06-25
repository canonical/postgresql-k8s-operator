[note]
**Note**: All commands are written for `juju >= v.3.0`

If you are using an earlier version, check the [Juju 3.0 Release Notes](https://juju.is/docs/juju/roadmap#heading--juju-3-0-0---22-oct-2022).
[/note]

# How to scale units

Replication in PostgreSQL is the process of creating copies of the stored data. This provides redundancy, which means the application can provide self-healing capabilities in case one replica fails. In this context, each replica is equivalent one juju unit.

This guide will show you how to establish and change the amount of juju units used to replicate your data. 

## Deploy PostgreSQL with replicas

To deploy PostgreSQL with multiple replicas, specify the number of desired units with the `-n` option:
```shell
juju deploy postgresql-k8s --channel 14/stable -n <number_of_replicas>
```

To retrieve the primary replica, use the action `get-primary` on any of the units running `postgresql-k8s`
```shell
juju run postgresql-k8s/leader get-primary
```

Similarly, the primary replica is displayed as a status message in `juju status`. However, one should note that this hook gets called on regular time intervals and the primary may be outdated if the status hook has not been called recently. 

### `update-status` hook
**We highly suggest configuring the status hook to run frequently.** In addition to reporting the primary, secondaries, and other statuses, the status hook performs self-healing in the case of a network cut. 

To change the frequency of the `update-status` hook, run
```shell
juju model-config update-status-hook-interval=<time(s/m/h)>
```
Note that this hook executes a read query to PostgreSQL. On a production level server, this should be configured to occur at a frequency that doesn't overload the server with read requests. Similarly, the hook should not be configured at too quick of a frequency, as this can delay other hooks from running. 

You can read more about status hooks [here](https://juju.is/docs/sdk/update-status-event).

## Scale replicas on an existing application

Both scaling up and scaling down operations are performed using `juju scale-application`:
```shell
juju scale-application postgresql-k8s <desired_num_of_units>
```
[note type=negative]
**Warning**: Scaling down to zero units will destroy your data!
[/note]