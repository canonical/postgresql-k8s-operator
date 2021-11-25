# PostgreSQL Kubernetes Operator

## Description

The PostgreSQL Kubernetes Operator deploys and operates the [PostgreSQL](https://www.postgresql.org/about/) database on Kubernetes clusters.

This operator provides a Postgres database with replication enabled (one master instance and one or more hot standby replicas). The Operator in this repository is a Python script which wraps the LTS Postgres versions distributed by [Ubuntu](https://hub.docker.com/r/ubuntu/postgres), providing lifecycle management and handling events (install, configure, integrate, remove).

## Usage

As this charm is not yet published, you need to follow the build and deploy instructions from [CONTRIBUTING.md](CONTRIBUTING.md).

## Accessing the database

You can access the database using any PostgreSQL client by connecting on the unit address and port `5432` as user `postgres` with the password shown by the command below.

```bash
juju run-action postgresql-k8s/0 get-postgres-password --wait
```