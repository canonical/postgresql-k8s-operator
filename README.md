# Charmed PostgreSQL K8s operator

[![CharmHub Badge](https://charmhub.io/postgresql-k8s/badge.svg)](https://charmhub.io/postgresql-k8s)
[![Release](https://github.com/canonical/postgresql-k8s-operator/actions/workflows/release.yaml/badge.svg)](https://github.com/canonical/postgresql-k8s-operator/actions/workflows/release.yaml)
[![Tests](https://github.com/canonical/postgresql-k8s-operator/actions/workflows/ci.yaml/badge.svg?branch=main)](https://github.com/canonical/postgresql-k8s-operator/actions/workflows/ci.yaml?query=branch%3Amain)
[![codecov](https://codecov.io/gh/canonical/postgresql-k8s-operator/graph/badge.svg?token=KmBJqV1AM2)](https://codecov.io/gh/canonical/postgresql-k8s-operator)

This repository contains a charmed operator for deploying [PostgreSQL](https://www.postgresql.org/about/) on Kubernetes via the [Juju orchestration engine](https://juju.is/).

To learn more about how to deploy and operate Charmed PostgreSQL K8s, see the [official documentation](https://canonical-charmed-postgresql-k8s.readthedocs-hosted.com/).

## Usage

Bootstrap a Kubernetes (e.g. [Multipass-based MicroK8s](https://discourse.charmhub.io/t/charmed-environment-charm-dev-with-canonical-multipass/8886)) and create a new model using Juju 3.6+:

```shell
juju add-model postgresql-k8s
juju deploy postgresql-k8s --channel 14/stable --trust
```

**Note:** the `--trust` flag is required because the charm and Patroni need to create some K8s resources.

**Note:** the above model must be created on K8s environment. Use [another](https://charmhub.io/postgresql) charm for VMs!

To confirm the deployment, you can run:

```shell
juju status --watch 1s
```

Once PostgreSQL starts up, it will be running on the default port (5432).

If required, you can remove the deployment completely by running:

```shell
juju destroy-model postgresql-k8s --destroy-storage --yes
```

**Note:** the `--destroy-storage` will delete any data persisted by PostgreSQL.

## Documentation

This operator provides a PostgreSQL database with replication enabled: one primary instance and one (or more) hot standby replicas. The Operator in this repository is a Python-based framework which wraps PostgreSQL distributed by Ubuntu Jammy and adding [Patroni](https://github.com/zalando/patroni) on top of it, providing lifecycle management and handling events (install, configure, integrate, remove, etc).

Please follow the [tutorial guide](https://discourse.charmhub.io/t/charmed-postgresql-k8s-documenation/9307) with detailed explanation how to access DB, configure cluster, change credentials and/or enable TLS.

## Integrations ([relations](https://juju.is/docs/olm/relations))

The charm supports modern `postgresql_client` and legacy `pgsql` interfaces (in a backward compatible mode).

**Note:** do NOT relate both modern and legacy interfaces simultaneously!


### Modern interfaces

This charm provides modern ['postgresql_client' interface](https://github.com/canonical/charm-relation-interfaces). Applications can easily connect PostgreSQL using ['data_interfaces' library](https://charmhub.io/data-platform-libs/libraries/data_interfaces) from ['data-platform-libs'](https://github.com/canonical/data-platform-libs/).

#### Modern `postgresql_client` interface (`database` endpoint):

Adding a relation is accomplished with `juju relate` (or `juju integrate` for Juju 3.x) via endpoint `database`. Example:

```shell
# Deploy Charmed PostgreSQL cluster with 3 nodes
juju deploy postgresql-k8s --channel 14/stable -n 3 --trust --channel 14

# Deploy the relevant application charms
juju deploy mycharm

# Relate PostgreSQL with your application
juju relate postgresql-k8s:database mycharm:database

# Check established relation (using postgresql_client interface):
juju status --relations

# Example of the properly established relation:
# > Relation provider          Requirer          Interface          Type
# > postgresql-k8s:database    mycharm:database  postgresql_client  regular
```

### Legacy interfaces

**Note:** Legacy relations are deprecated and will be discontinued on future releases. Usage should be avoided.

#### Legacy `pgsql` interface (`db` and `db-admin` endpoints):

This charm supports legacy interface `pgsql` from the previous [PostgreSQL charm](https://launchpad.net/postgresql-charm):

```shell
juju deploy postgresql-k8s --channel 14/stable --trust 
juju deploy finos-waltz-k8s --channel edge
juju relate postgresql-k8s:db finos-waltz-k8s
```

**Note:** The endpoint `db-admin` provides the same legacy interface `pgsql` with PostgreSQL admin-level privileges. It is NOT recommended to use it from security point of view.

## OCI Images

This charm uses pinned and tested version of the [charmed-postgresql](https://github.com/canonical/charmed-postgresql-rock/pkgs/container/charmed-postgresql) rock.

## Security

Security issues in the Charmed PostgreSQL K8s Operator can be reported through [private security reports](https://github.com/canonical/postgresql-k8s-operator/security/advisories/new) on GitHub.
For more information, see the [Security policy](SECURITY.md).

## Contributing

Please see the [Juju SDK docs](https://documentation.ubuntu.com/juju/3.6/) for guidelines on enhancements to this charm following best practice guidelines, and [CONTRIBUTING.md](https://github.com/canonical/postgresql-k8s-operator/blob/main/CONTRIBUTING.md) for developer guidance.

## License

The Charmed PostgreSQL K8s Operator [is distributed](https://github.com/canonical/postgresql-k8s-operator/blob/main/LICENSE) under the Apache Software License, version 2.0.
It installs/operates/depends on [PostgreSQL](https://www.postgresql.org/ftp/source/), which [is licensed](https://www.postgresql.org/about/licence/) under PostgreSQL License, a liberal Open Source license, similar to the BSD or MIT licenses.

## Trademark Notice

PostgreSQL is a trademark or registered trademark of PostgreSQL Global Development Group.
Other trademarks are property of their respective owners.
