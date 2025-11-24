# Interfaces/endpoints

The charm supports modern `postgresql_client` and legacy `pgsql` interfaces (in a backward compatible mode).

```{caution}
Do **not** relate both modern and legacy interfaces simultaneously!
```

## Modern interfaces

This charm provides modern ['postgresql_client'](https://github.com/canonical/charm-relation-interfaces) interface. Applications can easily connect PostgreSQL using ['data_interfaces'](https://charmhub.io/data-platform-libs/libraries/data_interfaces) library from ['data-platform-libs'](https://github.com/canonical/data-platform-libs/).

### Modern `postgresql_client` interface (`database` endpoint):

Adding a relation is accomplished with `juju relate` (or `juju integrate` for Juju 3.x) via endpoint `database`. Example:

```text
# Deploy Charmed PostgreSQL cluster with 3 nodes
juju deploy postgresql-k8s --channel 16/edge -n 3 --trust

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

Find all details about default and extra DB user roles in [](/explanation/users)".

## Legacy interfaces

```{note}
Legacy relations are deprecated and will be discontinued on future releases. Their usage should be avoided. 

Check the limitations of legacy interface implementations in [](/explanation/legacy-charm)
```

### Legacy `pgsql` interface (`db` and `db-admin` endpoints):

This charm supports legacy interface `pgsql` from the previous [PostgreSQL charm](https://launchpad.net/postgresql-charm):

```text
juju deploy postgresql-k8s --channel 16/edge --trust
juju deploy finos-waltz-k8s --channel edge
juju relate postgresql-k8s:db finos-waltz-k8s
```

```{note}
The endpoint `db-admin` provides the same legacy interface `pgsql` with PostgreSQL admin-level privileges. It is NOT recommended to use it due to security limitations.
```

