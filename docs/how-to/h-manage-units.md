# How to deploy and manage units

## Basic Usage

To deploy a single unit of PostgreSQL using its default configuration
```shell
juju deploy postgresql-k8s --channel 14/stable
```

It is customary to use PostgreSQL with replication. Hence usually more than one unit (preferably an odd number to prohibit a "split-brain" scenario) is deployed. To deploy PostgreSQL with multiple replicas, specify the number of desired units with the `-n` option.
```shell
juju deploy postgresql-k8s --channel 14/stable -n <number_of_replicas>
```

To retrieve primary replica one can use the action `get-primary` on any of the units running postgresql-k8s
```shell
juju run-action postgresql-k8s/leader get-primary --wait
```

Similarly, the primary replica is displayed as a status message in `juju status`, however one should note that this hook gets called on regular time intervals and the primary may be outdated if the status hook has not been called recently. 

Further we highly suggest configuring the status hook to run frequently. In addition to reporting the primary, secondaries, and other statuses, the status hook performs self healing in the case of a network cut. To change the frequency of the update status hook do:
```shell
juju model-config update-status-hook-interval=<time(s/m/h)>
```
Note that this hook executes a read query to PostgreSQL. On a production level server this should be configured to occur at a frequency that doesn't overload the server with read requests. Similarly the hook should not be configured at too quick of a frequency as this can delay other hooks from running. You can read more about status hooks [here](https://juju.is/docs/sdk/update-status-event).

## Replication

Both scaling-up and scaling-down operations are performed using `juju scale-application`:
```shell
juju scale-application postgresql-k8s <desired_num_of_units>
```

Warning: scaling-down to zero units will destroy your data!