# Troubleshooting

This page goes over some recommended tools and approaches to troubleshooting the charm.

Before anything, always run `juju status` to check the [list of charm statuses](/reference/statuses) and the recommended fixes. This alone may already solve your issue. 

Otherwise, this reference goes over how to troubleshoot this charm via:

- [`juju` logs](#juju-logs)
- [`kubectl`](#kubectl)
- The [`charm` container](#charm-container)
- The [`postgresql` workload container](#postgresql-workload-container)
- [Installing extra software](#install-extra-software)

```{caution}
At the moment, there is no support for [pausing an operator](https://warthogs.atlassian.net/browse/DPE-2545).

Make sure your activity will not interfere with the operator itself!
```

## Juju logs

See:
* [Juju | logs](https://juju.is/docs/juju/log)
* [Juju | How to manage logs](https://juju.is/docs/juju/manage-logs).

Always check the Juju logs before troubleshooting further:
```text
juju debug-log --replay --tail
```

Focus on `ERRORS` (normally there should be none):
```text
juju debug-log --replay | grep -c ERROR
```

Consider enabling the `DEBUG` log level if you are troubleshooting unusual charm behaviour:
```text
juju model-config 'logging-config=<root>=INFO;unit=DEBUG'
```

The Patroni/PostgreSQL logs are located in `workload` container:
```text
> ls -la /var/log/postgresql/
-rw-r--r-- 1 postgres postgres 23863 Sep 15 13:10 patroni.log
-rw------- 1 postgres postgres  2215 Sep 15 12:57 postgresql.log
```
If backups are enabled, Pgbackrest logs can also be found in the `workload` container:
```text
> ls -la /var/log/pgbackrest/
-rw-r----- 1 postgres postgres 2949 Sep 18 10:42 all-server.log
-rw-r----- 1 postgres postgres 3219 Sep 18 10:41 test3.patroni-postgresql-k8s-stanza-create.log
```
For more information about the `workload` container, see [](#postgresql-workload-container)

## `kubectl`

Check the operator [architecture](/explanation/architecture) first to become familiar with `charm` and `workload` containers. Make sure both containers are `Running` and `Ready` to continue troubleshooting inside the charm. 

To describe the running pod, use the following command (where `0` is a Juju unit id). :
```text
kubectl describe pod postgresql-k8s-0 -n <juju_model_name>
...
Containers:
  charm:
    ...
    Image:          jujusolutions/charm-base:ubuntu-22.04
    State:          Running
    Ready:          True
    Restart Count:  0
    ...
  postgresql:
    ...
    Image:          registry.jujucharms.com/charm/kotcfrohea62xreenq1q75n1lyspke0qkurhk/postgresql-image@something
    State:          Running
    Ready:          True
    Restart Count:  0
    ...
```

## Charm container

To enter the charm container, use:
```text
juju ssh postgresql-k8s/0 bash
```

Here you can make sure pebble is running. The Pebble plan is: 
```text
root@postgresql-k8s-0:/var/lib/juju# /charm/bin/pebble services
Service          Startup  Current  Since
container-agent  enabled  active   today at 12:29 UTC

root@postgresql-k8s-0:/var/lib/juju# ps auxww
USER         PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND
root           1  0.0  0.0 718264 10876 ?        Ssl  12:29   0:00 /charm/bin/pebble run --http :38812 --verbose
root          15  0.6  0.1 778628 59148 ?        Sl   12:29   0:03 /charm/bin/containeragent unit --data-dir /var/lib/juju --append-env PATH=$PATH:/charm/bin --show-log --charm-modified-version 0
```

If you have issues here, please [contact us](/reference/contacts).

Additionally, feel free to improve this document!

## `postgresql` workload container

To enter the workload container, use:
```text
juju ssh --container postgresql postgresql-k8s/0 bash
```
You can check the list of running processes and Pebble plan:

```text
root@postgresql-k8s-0:/# /charm/bin/pebble services
Service            Startup   Current   Since
metrics_server     enabled   active    today at 12:30 UTC
pgbackrest server  disabled  inactive  -
postgresql         enabled   active    today at 12:29 UTC

root@postgresql-k8s-0:/# ps auxww
USER         PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND
root           1  0.0  0.0 718264 10916 ?        Ssl  12:29   0:00 /charm/bin/pebble run --create-dirs --hold --http :38813 --verbose
postgres      14  0.1  0.1 565020 39412 ?        Sl   12:29   0:01 python3 /usr/bin/patroni /var/lib/pg/data/patroni.yml
postgres      30  0.0  0.0 1082704 9076 ?        Sl   12:30   0:00 /usr/bin/prometheus-postgres-exporter
postgres      48  0.0  0.0 215488 28912 ?        S    12:30   0:00 /usr/lib/postgresql/16/bin/postgres -D /var/lib/pg/data/16/main --config-file=/var/lib/pg/data/16/main/postgresql.conf --listen_addresses=0.0.0.0 --port=5432 --cluster_name=patroni-postgresql-k8s --wal_level=logical --hot_standby=on --max_connections=100 --max_wal_senders=10 --max_prepared_transactions=0 --max_locks_per_transaction=64 --track_commit_timestamp=off --max_replication_slots=10 --max_worker_processes=8 --wal_log_hints=on
postgres      50  0.0  0.0  70080  7488 ?        Ss   12:30   0:00 postgres: patroni-postgresql-k8s: logger 
postgres      52  0.0  0.0 215592  9136 ?        Ss   12:30   0:00 postgres: patroni-postgresql-k8s: checkpointer 
postgres      53  0.0  0.0 215604  9632 ?        Ss   12:30   0:00 postgres: patroni-postgresql-k8s: background writer 
postgres      54  0.0  0.0 215488 12208 ?        Ss   12:30   0:00 postgres: patroni-postgresql-k8s: walwriter 
postgres      55  0.0  0.0 216052 10928 ?        Ss   12:30   0:00 postgres: patroni-postgresql-k8s: autovacuum launcher 
postgres      56  0.0  0.0 215488  8420 ?        Ss   12:30   0:00 postgres: patroni-postgresql-k8s: archiver 
postgres      57  0.0  0.0  70196  8384 ?        Ss   12:30   0:00 postgres: patroni-postgresql-k8s: stats collector 
postgres      58  0.0  0.0 216032 10476 ?        Ss   12:30   0:00 postgres: patroni-postgresql-k8s: logical replication launcher 
postgres      60  0.0  0.0 218060 21428 ?        Ss   12:30   0:00 postgres: patroni-postgresql-k8s: operator postgres 127.0.0.1(59528) idle
```

The list of running Pebble services will depend on configured (enabled) [COS integration](/how-to/monitoring-cos/enable-monitoring) and/or [backup](/how-to/back-up-and-restore/create-a-backup) functionality. Pebble and its service `postgresql` must always be enabled and currently running (the Linux processes `pebble`, `patroni` and `postgres`).

To ssh into the PostgreSQL unit, check the [charm users concept](/explanation/users) and request admin credentials. Make sure you have `psql` installed.
```text
> juju run postgresql-k8s/leader get-password username=operator
password: 3wMQ1jzfuERvTEds

> juju ssh --container postgresql postgresql-k8s/0 bash

> > psql -h 127.0.0.1 -U operator -d postgres -W
> > Password for user operator: 3wMQ1jzfuERvTEds
>
> > postgres=# \l
> > postgres  | operator | UTF8     | C       | C.UTF-8 | operator=CTc/operator   +
> >           |          |          |         |         | backup=CTc/operator     +
> ...
```
Continue troubleshooting your database/SQL related issues from here.

```{caution}
To avoid split-brain scenarios:

* Do not manage users, credentials, databases, and schema directly. 
* Avoid restarting services directly. If you see the problem with a unit, consider [removing the failing unit and adding a new unit](/how-to/scale-replicas) to recover the cluster state.
```

[Contact us](/reference/contacts) if you cannot determine the source of your issue.

## Install extra software

We recommend you do **not** install any additional software. This may affect stability and produce anomalies that are hard to troubleshoot.

Sometimes, however, it is necessary to install some extra troubleshooting software. 

Use the common approach:

```text
root@postgresql-k8s-0:/# apt update && apt install less
...
Setting up less (590-1ubuntu0.22.04.1) ...
root@postgresql-k8s-0:/#
```

```{tip}
Always remove manually installed components at the end of troubleshooting. Keep the house clean!
```

