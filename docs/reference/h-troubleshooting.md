# Troubleshooting

[note type="caution"]
**Warning:** At the moment, there is **no** ability to [pause an operator](https://warthogs.atlassian.net/browse/DPE-2545).

Make sure your activity will not interfere with the operator itself!
[/note]

[note]
**Note**: All commands are written for `juju >= v.3.0`

If you are using an earlier version, be aware that:

 - `juju run` replaces `juju run-action --wait` in `juju v.2.9` 
 - `juju integrate` replaces `juju relate` and `juju add-relation` in `juju v.2.9`

For more information, check the [Juju 3.0 Release Notes](https://juju.is/docs/juju/roadmap#heading--juju-3-0-0---22-oct-2022).
[/note]

## Summary
This page goes over some recommended tools and approaches to troubleshooting the charm.

This reference goes over how to troubleshoot this charm via:
- [`juju` logs](#heading--logs)
- [`kubectl`](#heading--kubectl)
- The [`charm` container](#heading--container-charm)
- The [`postgresql` workload container](#heading--container-postgresql)
- [Installing extra software](#heading--install-extra-software)

[note]
Before anything, always run `juju status` to check the [list of charm statuses](/t/11855) and the recommended fixes. This alone may already solve your issue. 
[/note]
<a href="#heading--logs"><h2 id="heading--logs">`juju` logs</h2></a>

Ensure you are familiar with [Juju logs concepts](https://juju.is/docs/juju/log) and [how to manage Juju logs](https://juju.is/docs/juju/manage-logs).

Always check the Juju logs before troubleshooting further:
```shell
juju debug-log --replay --tail
```

Focus on `ERRORS` (normally there should be none):
```shell
juju debug-log --replay | grep -c ERROR
```

Consider enabling the `DEBUG` log level if you are troubleshooting unusual charm behaviour:
```shell
juju model-config 'logging-config=<root>=INFO;unit=DEBUG'
```

The Patroni/PostgreSQL logs are located in `workload` container:
```shell
> ls -la /var/log/postgresql/
-rw-r--r-- 1 postgres postgres 23863 Sep 15 13:10 patroni.log
-rw------- 1 postgres postgres  2215 Sep 15 12:57 postgresql.log
```
If backups are enabled, Pgbackrest logs can also be found in the `workload` container:
```shell
> ls -la /var/log/pgbackrest/
-rw-r----- 1 postgres postgres 2949 Sep 18 10:42 all-server.log
-rw-r----- 1 postgres postgres 3219 Sep 18 10:41 test3.patroni-postgresql-k8s-stanza-create.log
```
For more information about the `workload` container, see the [Container `postgresql` (workload)]() section below) section below.

<a href="#heading--kubectl"><h2 id="heading--kubectl">`kubectl`</h2></a>

Check the operator [architecture](/t/11856) first to become familiar with `charm` and `workload` containers. Make sure both containers are `Running` and `Ready` to continue troubleshooting inside the charm. 

To describe the running pod, use the following command (where `0` is a Juju unit id). :
```shell
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

<a href="#heading--container-charm"><h2 id="heading--container-charm">`charm` container</h2></a>

To enter the `charm` container, use:
```shell
juju ssh postgresql-k8s/0 bash
```

Here you can make sure pebble is running. The Pebble plan is: 
```shell
root@postgresql-k8s-0:/var/lib/juju# /charm/bin/pebble services
Service          Startup  Current  Since
container-agent  enabled  active   today at 12:29 UTC

root@postgresql-k8s-0:/var/lib/juju# ps auxww
USER         PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND
root           1  0.0  0.0 718264 10876 ?        Ssl  12:29   0:00 /charm/bin/pebble run --http :38812 --verbose
root          15  0.6  0.1 778628 59148 ?        Sl   12:29   0:03 /charm/bin/containeragent unit --data-dir /var/lib/juju --append-env PATH=$PATH:/charm/bin --show-log --charm-modified-version 0
```

If you have issues here, please [contact us](/t/11852).

Additionally, feel free to improve this document!

<a href="#heading--container-postgresql"><h2 id="heading--container-postgresql">`postgresql` workload container</h2></a>

To enter the workload container, use:
```shell
juju ssh --container postgresql postgresql-k8s/0 bash
```
You can check the list of running processes and Pebble plan:

```shell
root@postgresql-k8s-0:/# /charm/bin/pebble services
Service            Startup   Current   Since
metrics_server     enabled   active    today at 12:30 UTC
pgbackrest server  disabled  inactive  -
postgresql         enabled   active    today at 12:29 UTC

root@postgresql-k8s-0:/# ps auxww
USER         PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND
root           1  0.0  0.0 718264 10916 ?        Ssl  12:29   0:00 /charm/bin/pebble run --create-dirs --hold --http :38813 --verbose
postgres      14  0.1  0.1 565020 39412 ?        Sl   12:29   0:01 python3 /usr/bin/patroni /var/lib/postgresql/data/patroni.yml
postgres      30  0.0  0.0 1082704 9076 ?        Sl   12:30   0:00 /usr/bin/prometheus-postgres-exporter
postgres      48  0.0  0.0 215488 28912 ?        S    12:30   0:00 /usr/lib/postgresql/14/bin/postgres -D /var/lib/postgresql/data/pgdata --config-file=/var/lib/postgresql/data/pgdata/postgresql.conf --listen_addresses=0.0.0.0 --port=5432 --cluster_name=patroni-postgresql-k8s --wal_level=logical --hot_standby=on --max_connections=100 --max_wal_senders=10 --max_prepared_transactions=0 --max_locks_per_transaction=64 --track_commit_timestamp=off --max_replication_slots=10 --max_worker_processes=8 --wal_log_hints=on
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

The list of running Pebble services will depend on configured (enabled) [COS integration](/t/10812) and/or [backup](/t/9596) functionality. Pebble and its service `postgresql` must always be enabled and currently running (the Linux processes `pebble`, `patroni` and `postgres`).

To ssh into the PostgreSQL unit, check the [charm users concept](/t/10843) and request admin credentials. Make sure you have `psql` installed.
```shell
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
Continue troubleshooting your database/SQL related issues from here.<br/>
[note type="caution"]
**Warning**: Do **NOT** manage users, credentials, databases, schema directly. This avoids a split brain situation with the operator and integrated applications.
[/note]

It is NOT recommended to restart services directly as it might create a split brain situation with operator internal state. If you see the problem with a unit, consider [scaling down and re-scaling up](/t/9299) to recover the cluster state.

As a last resort, [contact us](/t/11852) if you cannot determine the source of your issue.

Also, feel free to improve this document!


<a href="#heading--install-extra-software"><h2 id="heading--install-extra-software">Install extra software</h2></a>

We recommend you do **not** install any additional software. This may affect stability and produce anomalies that are hard to troubleshoot.

Sometimes, however, it is necessary to install some extra troubleshooting software. 

Use the common approach:
```console
root@postgresql-k8s-0:/# apt update && apt install less
...
Setting up less (590-1ubuntu0.22.04.1) ...
root@postgresql-k8s-0:/#
```

**Always remove manually installed components at the end of troubleshooting.** Keep the house clean!