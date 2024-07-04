[note]
**Note**: All commands are written for `juju >= v.3.0`

If you are using an earlier version, check the [Juju 3.0 Release Notes](https://juju.is/docs/juju/roadmap#heading--juju-3-0-0---22-oct-2022).
[/note]

# How to manage client applications

[Integrations](https://juju.is/docs/juju/relation) (formerly "relations") are connections between two applications with compatible endpoints. These connections simplify the creation and management of users, passwords, and other shared data.

## Create an integration

**Integrations with new applications are supported via the [`postgresql_client`](https://github.com/canonical/charm-relation-interfaces/blob/main/interfaces/postgresql_client/v0/README.md) interface.** 

To create an integration, run
```shell
juju integrate postgresql-k8s application
```

To remove an integration:

```shell
juju remove-relation postgresql-k8s application
```

### Legacy `pgsql` interface

We have also added support for the database legacy relation from the [original version](https://launchpad.net/postgresql-charm) of the charm via the `pgsql` interface. Note that **this interface is deprecated**.

 ```shell
juju integrate postgresql-k8s:db mattermost-k8s:db
```

Extended permissions can be requested using the `db-admin` endpoint:
```shell
juju integrate postgresql-k8s:db-admin mattermost-k8s:db
```

## Rotate application passwords

To rotate the passwords of users created for integrated applications, the integration should be removed and integrated again. This process will generate a new user and password for the application.

```shell
juju remove-relation application postgresql-k8s
juju add-relation application postgresql-k8s
```

### Internal operator user
The `operator` user is used internally by the Charmed PostgreSQL K8s Operator. The `set-password` action can be used to rotate its password.

To set a specific password for the `operator `user, run
```shell
juju run postgresql-k8s/leader set-password password=<password>
```

To randomly generate a password for the `operator` user, run

```shell
juju run postgresql-k8s/leader set-password
```