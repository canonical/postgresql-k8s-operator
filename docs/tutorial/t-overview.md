# Charmed PostgreSQL K8s tutorial

The Charmed PostgreSQL K8s Operator delivers automated operations management from [day 0 to day 2](https://codilime.com/blog/day-0-day-1-day-2-the-software-lifecycle-in-the-cloud-age/) on the [PostgreSQL](https://www.postgresql-k8s.org/) relational database. It is an open source, end-to-end, production-ready data platform on top of Juju. As a first step this tutorial shows you how to get Charmed PostgreSQL K8s up and running, but the tutorial does not stop there. Through this tutorial you will learn a variety of operations, everything from adding replicas to advanced operations such as enabling Transport Layer Security (TLS). In this tutorial we will walk through how to:
- Set up an environment using [Multipass](https://multipass.run/) with [MicroK8s](https://microk8s.io/) and [Juju](https://juju.is/).
- Deploy PostgreSQL using a single command.
- Access the database directly.
- Add high availability with PostgreSQL Patroni-based cluster.
- Request and change passwords.
- Automatically create PostgreSQL users via Juju relations.
- Reconfigure TLS certificate in one command.

While this tutorial intends to guide and teach you as you deploy Charmed PostgreSQL K8s, it will be most beneficial if you already have a familiarity with:
- Basic terminal commands.
- PostgreSQL concepts such as replication and users.

## Step-by-step guide

Here’s an overview of the steps required with links to our separate tutorials that deal with each individual step:
* [Set up the environment](/t/charmed-postgresql-k8s-tutorial-setup-environment/9297?channel=14/stable)
* [Deploy PostgreSQL](/t/charmed-postgresql-k8s-tutorial-deploy/9298?channel=14/stable)
* [Managing your units](/t/charmed-postgresql-k8s-tutorial-managing-units/9299?channel=14/stable)
* [Manage passwords](/t/charmed-postgresql-k8s-tutorial-manage-passwords/9300?channel=14/stable)
* [Relate your PostgreSQL to other applications](/t/charmed-postgresql-k8s-tutorial-integrations/9301?channel=14/stable)
* [Enable security](/t/charmed-postgresql-k8s-tutorial-enable-security/9302?channel=14/stable)
* [Cleanup your environment](/t/charmed-postgresql-k8s-tutorial-cleanup/9303?channel=14/stable)

# License:
The Charmed PostgreSQL K8s Operator [is distributed](https://github.com/canonical/postgresql-k8s-operator/blob/main/LICENSE) under the Apache Software License, version 2.0. It installs/operates/depends on [PostgreSQL](https://www.postgresql-k8s.org/ftp/source/), which [is licensed](https://www.postgresql-k8s.org/about/licence/) under PostgreSQL License, a liberal Open Source license, similar to the BSD or MIT licenses..

## Trademark Notice
PostgreSQL is a trademark or registered trademark of PostgreSQL Global Development Group. Other trademarks are property of their respective owners.