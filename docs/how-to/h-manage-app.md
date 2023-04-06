# How to manage related applications

## New `postgresql_client` interface:

Relations to new applications are supported via the "[postgresql_client](https://github.com/canonical/charm-relation-interfaces/blob/main/interfaces/postgresql_client/v0/README.md)" interface. To create a relation: 

```shell
juju relate postgresql-k8s application
```

To remove a relation:

```shell
juju remove-relation postgresql-k8s application
```

## Legacy `pgsql` interface:

We have also added support for the database legacy relation from the [original version](https://launchpad.net/postgresql-charm) of the charm via the `pgsql` interface. Please note that these interface is deprecated.

 ```shell
juju relate postgresql-k8s:db mattermost-k8s:db
```

Also extended permissions can be requested using `db-admin` edpoint:
```shell
juju relate postgresql-k8s:db-admin mattermost-k8s:db
```


## Rotate applications password

To rotate the passwords of users created for related applications, the relation should be removed and related again. That process will generate a new user and password for the application.

```shell
juju remove-relation application postgresql-k8s
juju add-relation application postgresql-k8s
```

### Internal operator user

The operator user is used internally by the Charmed PostgreSQL Operator, the `set-password` action can be used to rotate its password.

* To set a specific password for the operator user

```shell
juju run-action postgresql-k8s/leader set-password password=<password> --wait
```

* To randomly generate a password for the operator user

```shell
juju run-action postgresql-k8s/leader set-password --wait
```