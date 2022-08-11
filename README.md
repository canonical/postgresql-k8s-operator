# PostgreSQL Kubernetes Operator

## Description

The PostgreSQL Kubernetes Operator deploys and operates the [PostgreSQL](https://www.postgresql.org/about/) database on Kubernetes clusters.

This operator provides a Postgres database with replication enabled (one master instance and one or more hot standby replicas). The Operator in this repository is a Python script which wraps the LTS Postgres versions distributed by [Ubuntu](https://hub.docker.com/r/ubuntu/postgres) and adding [Patroni](https://github.com/zalando/patroni) on top of it, providing lifecycle management and handling events (install, configure, integrate, remove).

## Usage

To deploy this charm using Juju 2.9.0 or later, run:

```shell
juju add-model postgresql
charmcraft pack
juju deploy ./postgresql-k8s_ubuntu-20.04-amd64.charm
```

Note: the above model must exist inside a k8s cluster (you can use juju bootstrap to create a controller in the k8s cluster).

To confirm the deployment, you can run:

```shell
juju status --color
```

Once PostgreSQL starts up, it will be running on the default port (5432).

If required, you can remove the deployment completely by running:

```shell
juju destroy-model -y postgresql --destroy-storage
```

Note: the `--destroy-storage` will delete any data persisted by PostgreSQL.

## Relations

We have added support for two legacy relations (from the [original version](https://launchpad.net/charm-k8s-postgresql) of the charm):

1. `db` is a relation that one uses when it is needed only a new database and a user with permissions on it. The following commands can be executed to deploy and relate to the FINOS Waltz Server charm:

```shell
# Pack the charm
charmcraft pack

# Deploy the relevant charms
juju deploy ./postgresql-k8s_ubuntu-20.04-amd64.charm \
     --resource postgresql-image=dataplatformoci/postgres-patroni
     -n 3 --trust
juju deploy finos-waltz-k8s

# Reduce the update status frequency to speed up nodes being added to the cluster.
juju model-config update-status-hook-interval=10s

# Relate FINOS Waltz Server with PostgreSQL
juju relate finos-waltz-k8s postgresql:shared-db
```

1. `db-admin` is a relation that one uses when the application needs to connect to the database cluster with superuser privileges. The following commands can be executed to deploy and relate to the Discourse charm:

```shell
# Pack the charm
charmcraft pack

# Deploy the relevant charms
juju ./postgresql-k8s_ubuntu-20.04-amd64.charm \
     --resource postgresql-image=dataplatformoci/postgres-patroni
     -n 3 --trust
juju deploy discourse-k8s

# Reduce the update status frequency to speed up nodes being added to the cluster.
juju model-config update-status-hook-interval=10s

# Relate Discourse with PostgreSQL
juju relate discourse-k8s postgresql-k8s:db-admin
```

## Security
Security issues in the Charmed PostgreSQL k8s Operator can be reported through [LaunchPad](https://wiki.ubuntu.com/DebuggingSecurity#How%20to%20File). Please do not file GitHub issues about security issues.

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines on enhancements to this charm following best practice guidelines, and [CONTRIBUTING.md](https://github.com/canonical/postgresql-k8s-operator/blob/main/CONTRIBUTING.md) for developer guidance.

## License
The Charmed PostgreSQL k8s Operator is free software, distributed under the Apache Software License, version 2.0. See [LICENSE](https://github.com/canonical/postgresql-k8s-operator/blob/main/LICENSE) for more information.
