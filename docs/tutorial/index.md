# Tutorial

This hands-on tutorial aims to help you learn how to deploy Charmed PostgreSQL on machines and become familiar with its available operations.

## Prerequisites

While this tutorial intends to guide you as you deploy Charmed PostgreSQL for the first time, it will be most beneficial if:
- You have some experience using a Linux-based CLI
- You are familiar with PostgreSQL concepts such as replication and users.
- Your computer fulfils the [minimum system requirements](/reference/system-requirements)

---

## Set up the environment

First, we will set up a cloud environment using [Multipass](https://multipass.run/) with [LXD](https://documentation.ubuntu.com/lxd/latest/) and [Juju](https://documentation.ubuntu.com/juju/3.6/). This is the quickest and easiest way to get your machine ready for using Charmed PostgreSQL. 

To learn about other types of deployment environments and methods (e.g. bootstrapping other clouds, using Terraform), see [](/how-to/deploy/index).

### Multipass

[Multipass](https://multipass.run/) is a quick and easy way to launch virtual machines running Ubuntu. It uses the [cloud-init](https://cloud-init.io/) standard to install and configure all the necessary parts automatically.

Install Multipass from the [snap store](https://snapcraft.io/multipass):
```text
sudo snap install multipass
```

Spin up a new VM using [`multipass launch`](https://multipass.run/docs/launch-command) with the [charm-dev](https://github.com/canonical/multipass-blueprints/blob/main/v1/charm-dev.yaml) cloud-init configuration:

```text
multipass launch --cpus 4 --memory 8G --disk 50G --name my-vm charm-dev
```

As soon as a new VM has started, access it:

```text
multipass shell my-vm
```

```{tip}
If at any point you'd like to leave a Multipass VM, enter `Ctrl+D` or type `exit`.
```

All necessary components have been pre-installed inside VM already, like LXD and Juju. The files `/var/log/cloud-init.log` and `/var/log/cloud-init-output.log` contain all low-level installation details. 

### Juju

Let's bootstrap Juju to use the local MicroK8s controller. We will call it "overlord", but you can give it any name you'd like:

```text
juju bootstrap microk8s overlord
```

A controller can work with different [models](https://juju.is/docs/juju/model). Set up a specific model for Charmed PostgreSQL K8s named `tutorial`:

```text
juju add-model tutorial
```

You can now view the model you created above by running the command `juju status`. You should see something similar to the following example output:

```
Model     Controller  Cloud/Region        Version  SLA          Timestamp
tutorial  overlord    microk8s/localhost  3.1.7    unsupported  11:56:38+01:00

Model "admin/tutorial" is empty.
```

## Deploy PostgreSQL

To deploy Charmed PostgreSQL K8s, run

```text
juju deploy postgresql-k8s --channel=14/stable --trust
```

Juju will now fetch Charmed PostgreSQL K8s from [Charmhub][Charmhub PostgreSQL K8s] and deploy it to the local MicroK8s. This process can take several minutes depending on how provisioned (RAM, CPU, etc) your machine is. 

You can track the progress by running:

```text
juju status --watch 1s
```

This command is useful for checking the real-time information about the state of a charm and the machines hosting it. Check the [`juju status` documentation](https://juju.is/docs/juju/juju-status) for more information about its usage.

When the application is ready, `juju status` will show something similar to the sample output below:

```
Model     Controller  Cloud/Region        Version  SLA          Timestamp
tutorial  charm-dev   microk8s/localhost  2.9.42   unsupported  12:00:43+01:00

App             Version  Status  Scale  Charm           Channel    Rev  Address         Exposed  Message
postgresql-k8s           active      1  postgresql-k8s  14/stable  56   10.152.183.167  no

Unit               Workload  Agent  Address       Ports  Message
postgresql-k8s/0*  active    idle   10.1.188.206
```
You can also watch juju logs with the [`juju debug-log`](https://juju.is/docs/juju/juju-debug-log) command.

## Access PostgreSQL

In this section, you will learn how to get the credentials of your deployment, connect to the PostgreSQL instance, view its default databases, and finally, create your own new database.

```{caution}
This part of the tutorial accesses PostgreSQL via the `operator` user. 

**Do not directly interface with the `operator` user in a production environment.**

In a later section about [integrations](#integrate-with-other-applications), we will cover how to safely access PostgreSQL by creating a separate user.
```

### Retrieve credentials

Connecting to the database requires that you know two pieces of information: 
1. The internal PostgreSQL database user credentials (username and password)
2. The host machine's IP address. 

Check the IP addresses associated with each application unit with the `juju status` command. 

Since we will use the leader unit to connect to PostgreSQL, we are interested in the IP address for the unit marked with `*`, like in the output below:

```text
Unit           	  Workload  Agent  Address   Ports  Message
postgresql-k8s/0*  active	idle   10.1.110.80     	Primary
```

The user we will connect to in this tutorial will be 'operator'. To retrieve its associated password, run the juju action `get-password`:

```text
juju run postgresql-k8s/leader get-password
```

The command above should output something like this:

```text
Running operation 1 with 1 task
  - task 2 on unit-postgresql-k8s-0

Waiting for task 2...
password: 66hDfCMm3ofT0yrG
```

In order to retrieve the password of a user other than `operator`, use the `username` option:

```text
juju run postgresql-k8s/leader get-password username=replication
```

At this point, we have all the information required to access PostgreSQL. Run the command below to enter the leader unit's shell as root:

```text
juju ssh --container postgresql postgresql-k8s/leader bash
```
which should bring you to a prompt like this: 

```text
root@postgresql-k8s-0:/#
```

```{tip}
If you’d like to leave the unit's shell and return to your local terminal, enter `Ctrl+D` or type `exit`.
```

### Access PostgreSQL via `psql`

The easiest way to interact with PostgreSQL is via [PostgreSQL interactive terminal `psql`](https://www.postgresql.org/docs/14/app-psql.html), which is already installed on the host you're connected to.

While still in the leader unit's shell, run the command below to list all databases currently available:

```text
psql --host=10.1.110.80 --username=operator --password --list
```

When requested, enter the password that you obtained earlier.

You can see below the output for the list of databases. `postgres` is the default database we are connected to and is used for administrative tasks and for creating other databases:

```text
   Name    |  Owner   | Encoding |   Collate   |    Ctype    |   Access privileges
-----------+----------+----------+-------------+-------------+-----------------------
 postgres  | operator | UTF8     | en_US.UTF-8 | en_US.UTF-8 |
 template0 | operator | UTF8     | en_US.UTF-8 | en_US.UTF-8 | =c/operator          +
           |          |          |             |             | operator=CTc/operator
 template1 | operator | UTF8     | en_US.UTF-8 | en_US.UTF-8 | =c/operator          +
           |          |          |             |             | operator=CTc/operator
(3 rows)
```

In order to execute queries, we should enter psql's interactive terminal by running the following command, again typing password when requested:

```text
 psql --host=10.1.110.80 --username=operator --password postgres
```

The output should be something like this:

```text
psql (14.10 (Ubuntu 14.10-0ubuntu0.22.04.1))
Type "help" for help.

postgres=## 
```

Now you are successfully logged in the interactive terminal. Here it is possible to execute commands to PostgreSQL directly using PostgreSQL SQL Queries. For example, to show which version of PostgreSQL is installed, run the following command:

```text
postgres=## SELECT version();
                                                             version
---------------------------------------------------------------------------------------------------------------------------------
 PostgreSQL 14.10 (Ubuntu 14.10-0ubuntu0.22.04.1) on x86_64-pc-linux-gnu, compiled by gcc (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0, 64-bit
(1 row)
```

We can see that PostgreSQL version 14.10 is installed. From this prompt, to print the list of available databases, we can simply run this command:

```text
postgres=## \l
```

The output should be the same as the one obtained before with `psql`, but this time we did not need to specify any parameters since we are already connected to the PostgreSQL application.

#### Create a new database

To create and connect to a new sample database, we can run the following commands:

```text
postgres=## CREATE DATABASE mynewdatabase;
postgres=## \c mynewdatabase

You are now connected to database "mynewdatabase" as user "operator".
```

We can now create a new table inside this database:

```text
postgres=## CREATE TABLE mytable (
	id SERIAL PRIMARY KEY,
	name VARCHAR(50),
	age INT
);
```

and insert an element into it:

```text
postgres=## INSERT INTO mytable (name, age) VALUES ('John', 30);
```

We can see our new table element by submitting a query:

```text
postgres=## SELECT * FROM mytable;

 id | name | age
----+------+-----
  1 | John |  30
(1 row)
```

You can try multiple SQL commands inside this environment. Once you're ready, reconnect to the default postgres database and drop the sample database we created:

```text
postgres=## \c postgres

You are now connected to database "postgres" as user "operator".
postgres=## DROP DATABASE mynewdatabase;
```

When you’re ready to leave the PostgreSQL shell, you can just type `exit`. This will take you back to the host of Charmed PostgreSQL K8s (`postgresql-k8s/0`). Exit this host by once again typing exit. Now you will be in your original shell where you first started the tutorial. Here you can interact with Juju and MicroK8s.

## Scale your replicas

The Charmed PostgreSQL VM operator uses a [PostgreSQL Patroni-based cluster](https://patroni.readthedocs.io/en/latest/) for scaling. It provides features such as automatic membership management, fault tolerance, and automatic failover. The charm uses PostgreSQL’s [synchronous replication](https://patroni.readthedocs.io/en/latest/replication_modes.html#postgresql-k8s-synchronous-replication) with Patroni to handle replication.

```{caution}
This tutorial hosts all replicas on the same machine. 

**This should not be done in a production environment.** 

To enable high availability in a production environment, replicas should be hosted on different servers to [maintain isolation](https://canonical.com/blog/database-high-availability).
```

### Add units

Currently, your deployment has only one juju **unit**, known in juju as the **leader unit**. You can think of this as the database **primary instance**. For each **replica**, a new unit is created. All units are members of the same database cluster.

To add two replicas to your deployed PostgreSQL application, use `juju scale-application` to scale it to three units:

```text
juju scale-application postgresql-k8s 3
```

```{note}
Unlike machine models, Kubernetes models use `juju scale-application` instead of `juju add-unit` and `juju remove-unit`.

For more information about juju's scaling logic for kubernetes, check [this post](https://discourse.charmhub.io/t/adding-removing-units-scale-application-command/153).
```

You can now watch the scaling process in live using: `juju status --watch 1s`. It usually takes several minutes for new cluster members to be added. 

You’ll know that all three nodes are in sync when `juju status` reports `Workload=active` and `Agent=idle`:
```text
Model     Controller  Cloud/Region        Version  SLA          Timestamp
tutorial  charm-dev   microk8s/localhost  2.9.42   unsupported  12:09:49+01:00

App             Version  Status  Scale  Charm           Channel    Rev  Address         Exposed  Message
postgresql-k8s           active      3  postgresql-k8s  14/stable  56   10.152.183.167  no

Unit               Workload  Agent  Address       Ports  Message
postgresql-k8s/0*  active    idle   10.1.188.206         Primary
postgresql-k8s/1   active    idle   10.1.188.209
postgresql-k8s/2   active    idle   10.1.188.210
```

### Remove units

Removing a unit from the application scales down the replicas.

Before we scale them down, list all the units with `juju status`. You will see three units:  `postgresql-k8s/0`, `postgresql-k8s/1`, and `postgresql-k8s/2`. Each of these units hosts a PostgreSQL replica. 

To scale the application down to two units, enter:

```text
juju scale-application postgresql-k8s 2
```

You’ll know that the replica was successfully removed when `juju status --watch 1s` reports:
```
Model     Controller  Cloud/Region        Version  SLA          Timestamp
tutorial  charm-dev   microk8s/localhost  2.9.42   unsupported  12:10:08+01:00

App             Version  Status  Scale  Charm           Channel    Rev  Address         Exposed  Message
postgresql-k8s           active      2  postgresql-k8s  14/stable  56   10.152.183.167  no

Unit               Workload  Agent  Address       Ports  Message
postgresql-k8s/0*  active    idle   10.1.188.206         Primary
postgresql-k8s/1   active    idle   10.1.188.209
```

## Manage passwords

When we accessed PostgreSQL earlier in this tutorial, we needed to use a password manually. Passwords help to secure our database and are essential for security. Over time, it is a good practice to change the password frequently. 

### Retrieve the operator password

The operator's password can be retrieved by running the `get-password` action on the PostgreSQL application:

```text
juju run postgresql-k8s/leader get-password
```

Running the command above should output something like:
```yaml
unit-postgresql-k8s-0:
  UnitId: postgresql-k8s/0
  id: "6"
  results:
    password: SYhCduijXTAfg9mU
  status: completed
  timing:
    completed: 2023-03-20 11:10:33 +0000 UTC
    enqueued: 2023-03-20 11:10:32 +0000 UTC
    started: 2023-03-20 11:10:33 +0000 UTC
```

### Rotate the operator password

You can change the operator's password to a new random password by entering:

```text
juju run postgresql-k8s/leader set-password
```

Running the command above should output something like:

```yaml
unit-postgresql-k8s-0:
  UnitId: postgresql-k8s/0
  id: "8"
  results:
    password: 7CYrRiBrC4du3ToX
  status: completed
  timing:
    completed: 2023-03-20 11:10:47 +0000 UTC
    enqueued: 2023-03-20 11:10:46 +0000 UTC
    started: 2023-03-20 11:10:47 +0000 UTC
```

The `status: completed` element in the output above indicates that the password has been successfully updated. The new password should be different from the previous password.

### Set a new password

You can set a specific password for any user by running the `set-password` juju action on the leader unit.   

To set a manual password for the `operator` user, run the following command:

```text
juju run postgresql-k8s/leader set-password password=<password>
```
where `<password>` is your password of choice.

Example output:

```yaml
unit-postgresql-k8s-0:
  UnitId: postgresql-k8s/0
  id: "10"
  results:
    password: my-password
  status: completed
  timing:
    completed: 2023-03-20 11:11:06 +0000 UTC
    enqueued: 2023-03-20 11:11:02 +0000 UTC
    started: 2023-03-20 11:11:05 +0000 UTC
```

Learn more about internal operator users in [](/explanation/users).

<!--Links-->

[Charmhub PostgreSQL K8s]: https://charmhub.io/postgresql-k8s?channel=14/stable