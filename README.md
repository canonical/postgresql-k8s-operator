# PostgreSQL Kubernetes Operator

## Description

The PostgreSQL Kubernetes Operator deploys and operates the [PostgreSQL](https://www.postgresql.org/about/) database on Kubernetes clusters.

This operator provides a Postgres database with replication enabled (one master instance and one or more hot standby replicas). The Operator in this repository is a Python script which wraps the LTS Postgres versions distributed by [Ubuntu](https://hub.docker.com/r/ubuntu/postgres) and adding [Patroni](https://github.com/zalando/patroni) on top of it, providing lifecycle management and handling events (install, configure, integrate, remove).

## Usage

### Basic Usage
To deploy a single unit of PostgreSQL using its default configuration.
```shell
juju deploy postgresql-k8s --channel edge --trust
```

Note: `--trust` is required because the charm and Patroni need to create some k8s resources.

It is customary to use PostgreSQL with replication. Hence usually more than one unit (preferably an odd number to prohibit a "split-brain" scenario) is deployed. To deploy PostgreSQL with multiple replicas, specify the number of desired units with the `-n` option.
```shell
juju deploy postgresql-k8s --channel edge -n <number_of_replicas> --trust
```

To retrieve primary replica one can use the action `get-primary` on any of the units running PostgreSQL.
```shell
juju run-action postgresql-k8s/<unit_number> get-primary --wait
```

Similarly, the primary replica is displayed as a status message in `juju status`, however one should note that this hook gets called on regular time intervals and the primary may be outdated if the status hook has not been called recently.

## Relations

Supported [relations](https://juju.is/docs/olm/relations):

#### New `postgresql_client` interface:

Relations to new applications are supported via the `postgresql_client` interface. To create a relation: 

```shell
juju relate postgresql-k8s application
```

To remove a relation:
```shell
juju remove-relation postgresql-k8s application
```

#### Legacy `pgsql` interface:
We have also added support for the two database legacy relations from the [original version](https://launchpad.net/charm-k8s-postgresql) of the charm via the `pgsql` interface. Please note that these relations will be deprecated.
 ```shell
juju relate postgresql-k8s:db-admin discourse-k8s
```

## Security
Security issues in the Charmed PostgreSQL k8s Operator can be reported through [LaunchPad](https://wiki.ubuntu.com/DebuggingSecurity#How%20to%20File). Please do not file GitHub issues about security issues.

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines on enhancements to this charm following best practice guidelines, and [CONTRIBUTING.md](https://github.com/canonical/postgresql-k8s-operator/blob/main/CONTRIBUTING.md) for developer guidance.

## License
The Charmed PostgreSQL k8s Operator is free software, distributed under the Apache Software License, version 2.0. See [LICENSE](https://github.com/canonical/postgresql-k8s-operator/blob/main/LICENSE) for more information.
