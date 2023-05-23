# Get a Charmed PostgreSQL up and running

This is part of the [Charmed PostgreSQL Tutorial](/t/charmed-postgresql-k8s-tutorial-overview/9296?channel=14/stable). Please refer to this page for more information and the overview of the content.

## Deploy

To deploy Charmed PostgreSQL K8s, all you need to do is run the following command, which will fetch the charm from [Charmhub](https://charmhub.io/postgresql-k8s?channel=14/stable) and deploy it to your model:
```shell
juju deploy postgresql-k8s --channel 14/stable --trust
```
Note: `--trust` is required because the charm and Patroni need to create some K8s resources.

Juju will now fetch Charmed PostgreSQL K8s and begin deploying it to the local MicroK8s. This process can take several minutes depending on how provisioned (RAM, CPU, etc) your machine is. You can track the progress by running:
```shell
juju status --watch 1s
```

This command is useful for checking the status of Charmed PostgreSQL K8s and gathering information about the machines hosting Charmed PostgreSQL K8s. Some of the helpful information it displays include IP addresses, ports, state, etc. The command updates the status of Charmed PostgreSQL K8s every second and as the application starts you can watch the status and messages of Charmed PostgreSQL K8s change. Wait until the application is ready - when it is ready, `juju status` will show:
```
Model     Controller  Cloud/Region        Version  SLA          Timestamp
tutorial  charm-dev   microk8s/localhost  2.9.42   unsupported  12:00:43+01:00

App             Version  Status  Scale  Charm           Channel    Rev  Address         Exposed  Message
postgresql-k8s           active      1  postgresql-k8s  14/stable  56   10.152.183.167  no

Unit               Workload  Agent  Address       Ports  Message
postgresql-k8s/0*  active    idle   10.1.188.206
```
To exit the screen with `juju status --watch 1s`, enter `Ctrl+c`.
If you want to further inspect juju logs, can watch for logs with `juju debug-log`.
More info on logging at [juju logs](https://juju.is/docs/olm/juju-logs).

## Access PostgreSQL
> **!** *Disclaimer: this part of the tutorial accesses PostgreSQL via the `operator` user. **Do not** directly interface with this user in a production environment. In a production environment always create a separate user using [Data Integrator](https://charmhub.io/data-integrator) and connect to PostgreSQL with that user instead. Later in the section covering Relations we will cover how to access PostgreSQL without the `operator` user.*

The first action most users take after installing PostgreSQL is accessing PostgreSQL. The easiest way to do this is via the [PostgreSQL interactive terminal](https://www.postgresql.org/docs/14/app-psql.html) `psql`. Connecting to the database requires that you know the values for `host`, `username` and `password`. To retrieve the necessary fields please run Charmed PostgreSQL K8s action `get-password`:
```shell
juju run-action postgresql-k8s/leader get-password --wait
```
Running the command should output:
```yaml
unit-postgresql-k8s-0:
  UnitId: postgresql-k8s/0
  id: "2"
  results:
    password: SYhCduijXTAfg9mU
  status: completed
  timing:
    completed: 2023-03-20 11:01:26 +0000 UTC
    enqueued: 2023-03-20 11:01:24 +0000 UTC
    started: 2023-03-20 11:01:25 +0000 UTC
```

*Note: to request a password for a different user, use an option `username`:*
```shell
juju run-action postgresql-k8s/leader get-password username=replication --wait
```

The host’s IP address can be found with `juju status` (the unit hosting the PostgreSQL application):
```
...
Unit               Workload  Agent  Address       Ports  Message
postgresql-k8s/0*  active    idle   10.1.188.206
...
```

To access the units hosting Charmed PostgreSQL K8s use:
```shell
juju ssh --container postgresql postgresql-k8s/leader bash
```
*Note: if at any point you'd like to leave the unit hosting Charmed PostgreSQL K8s, enter* `Ctrl+d` or type `exit`*.

The `psql` tool is already installed here. To show list of all available databases use:
```shell
psql --host=10.1.188.206 --username=operator --password --list
```
*Note: when requested, enter the `<password>` for charm user `operator` from the output above.*

The example of the output:
```
                                  List of databases
   Name    |  Owner   | Encoding |   Collate   |    Ctype    |   Access privileges
-----------+----------+----------+-------------+-------------+-----------------------
 postgres  | operator | UTF8     | en_US.UTF-8 | en_US.UTF-8 |
 template0 | operator | UTF8     | en_US.UTF-8 | en_US.UTF-8 | =c/operator          +
           |          |          |             |             | operator=CTc/operator
 template1 | operator | UTF8     | en_US.UTF-8 | en_US.UTF-8 | =c/operator          +
           |          |          |             |             | operator=CTc/operator
(3 rows)
```

You can now interact with PostgreSQL directly using any [PostgreSQL SQL Queries](https://www.postgresql-k8s.org/docs/14/queries.html). For example entering `SELECT version();` should output something like:
```
> root@postgresql-k8s-0:/# psql --host=10.1.188.206 --username=operator --password postgres
Password:
psql (14.5 (Ubuntu 14.5-0ubuntu0.22.04.1))
Type "help" for help.

postgres=# SELECT version();
                                                             version
---------------------------------------------------------------------------------------------------------------------------------
 PostgreSQL 14.5 (Ubuntu 14.5-0ubuntu0.22.04.1) on x86_64-pc-linux-gnu, compiled by gcc (Ubuntu 11.2.0-19ubuntu1) 11.2.0, 64-bit
(1 row)
```
*Note: if at any point you'd like to leave the PostgreSQL client, enter `Ctrl+d` or type `exit`*.

Feel free to test out any other PostgreSQL queries. When you’re ready to leave the PostgreSQL shell you can just type `exit`. Once you've typed `exit` you will be back in the host of Charmed PostgreSQL K8s (`postgresql-k8s/0`). Exit this host by once again typing `exit`. Now you will be in your original shell where you first started the tutorial; here you can interact with Juju and MicroK8s.