# PostgreSQL Kubernetes Operator

## Overview

This repository hosts the PostgreSQL Kubernetes Operator.

## Description

The PostgreSQL Kubernetes Operator deploys and operates the [PostgreSQL](https://www.postgresql.org/about/) database on Kubernetes clusters.

This operator provides a Postgres database with replication enabled (one master instance and one or more hot standby replicas). The Operator in this repository is a Python script which wraps the LTS Postgres versions distributed by [Ubuntu](https://hub.docker.com/r/ubuntu/postgres), providing lifecycle management and handling events (install, configure, integrate, remove).

## Usage

In order to install this charm, execute the following commands (replace `SECRET_PASSWORD` with a strong password):

```bash
# Clone this charm respository
$ git clone https://github.com/canonical/postgresql-k8s-operator
$ cd postgresql-k8s-operator
# Build the charm
$ charmcraft pack
# Create a model for our deployment
$ juju add-model postgres
# Deploy the charm (providing a password for the postgres user)
$ juju deploy ./postgresql-k8s-operator*.charm \
    --resource postgresql-image=ubuntu/postgres \
    --config postgres-password=SECRET_PASSWORD
# Wait for the deployment to complete
$ watch -n1 --color "juju status --color"
```

The deployment process will take a few moments. You should end up with some output like the following:

```
❯ juju status
Model     Controller  Cloud/Region        Version  SLA          Timestamp
postgres  micro       microk8s/localhost  2.9.18   unsupported  14:46:27-03:00

App                      Version  Status  Scale  Charm                    Store  Channel  Rev  OS          Address        Message
postgresql-k8s-operator           active      1  postgresql-k8s-operator  local             1  kubernetes  10.152.183.42  

Unit                        Workload  Agent  Address      Ports  Message
postgresql-k8s-operator/0*  active    idle   10.1.65.207
```

You can now access the database (using the example above) by connecting as user `postgres` with the password `SECRET_PASSWORD` on host `10.1.65.207` and port `5432`.