# How to manage passwords

In Charmed PostgreSQL 14, user credentials are managed with Juju's `get-password` and `set-password` actions.

## Get password

To retrieve the operator's password:

```text
juju run postgresql-k8s/leader get-password
```

## Set password

To change the operator's password to a new, randomised password:
```text
juju run postgresql-k8s/leader set-password
```

To set a manual password for the operator/admin user:

```text
juju run postgresql-k8s/leader set-password password=<password>
```

To set a manual password for another user:

```text
juju run postgresql-k8s/leader set-password username=<username> password=<password>
```

