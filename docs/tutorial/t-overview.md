# Charmed PostgreSQL K8s Tutorial

This section of our documentation contains comprehensive, hands-on tutorials to help you learn how to deploy Charmed PostgreSQL K8s and become familiar with its available operations.

## Prerequisites

While this tutorial intends to guide you as you deploy Charmed PostgreSQL K8s for the first time, it will be most beneficial if:
- You have some experience using a Linux-based CLI
- You are familiar with PostgreSQL concepts such as replication and users.
- Your computer fulfills the [minimum system requirements](/t/11744)

## Tutorial contents
This Charmed PostgreSQL K8s tutorial has the following parts:

| Step | Details |
| ------- | ---------- |
| 1. [**Set up the environment**](/t/9297) | Set up a cloud environment for your deployment using [Multipass](https://multipass.run/) with [MicroK8s](https://microk8s.io/) and [Juju](https://juju.is/).
| 2. [**Deploy PostgreSQL**](/t/9298) |    Learn to deploy Charmed PostgreSQL K8s using a single command and access the database directly.
| 3. [**Access PostgreSQL**](/t/13702) |   Learn how to access a PostgreSQL instance directly
| 4. [**Scale the amount of replicas**](/t/9299) | Learn how to enable high availability with a [Patroni](https://patroni.readthedocs.io/en/latest/)-based cluster.
| 5. [**Manage passwords**](/t/9300) | Learn how to request and change passwords.
| 6. [**Integrate PostgreSQL with other applications**](/t/9301) | Learn how to integrate with other applications using the Data Integrator charm, access the database from a client application, and manage users.
| 7. [**Enable TLS encryption**](/t/9302) | Learn how to enable security in your PostgreSQL deployment via TLS.
| 8. [**Clean-up your environment**](/t/9303) | Free up your machine's resources.