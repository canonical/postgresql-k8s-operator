# How to connect DB from outside of Kubernetes

It is possible to connect to a client application outside the database's Kubernetes cluster. The method depends on whether it is a non-Juju application or a Juju application.

## External K8s application (non-Juju)

**Use case**: The client application is a non-Juju application outside of DB K8s deployment.

To connect the Charmed PostgreSQL K8s database from outside the Kubernetes cluster, the charm PgBouncer K8s should be deployed. Please follow the instructions in the [PgBouncer K8s documentation](https://charmhub.io/pgbouncer-k8s/docs/h-external-access).

## External K8s relation (Juju)

**Use case**: The client application is a Juju application outside of DB K8s deployment (e.g. hybrid Juju deployment with mixed K8s and VM applications).

In this case, a cross-hybrid-relation is necessary. Please [contact](/reference/contacts) Data team to discuss the possible options for your use case.

