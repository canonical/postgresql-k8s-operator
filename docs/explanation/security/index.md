# Security hardening guide

This document provides an overview of security features and guidance for hardening the security of [Charmed PostgreSQL K8s](https://charmhub.io/postgresql-k8s) deployments, including setting up and managing a secure environment.

## Environment

The environment where Charmed PostgreSQL K8s operates can be divided into two components:

1. Kubernetes
2. Juju

### Kubernetes

Charmed PostgreSQL K8s can be deployed on top of several Kubernetes distributions. The following table provides references for the security documentation for the main supported cloud platforms.

|Cloud|Security guides|
| --- | --- |
|Canonical Kubernetes|[Security overview](https://ubuntu.com/kubernetes/docs/security), [How to secure a cluster](https://ubuntu.com/kubernetes/docs/how-to-security)|
|MicroK8s|[CIS compliance](https://microk8s.io/docs/cis-compliance), [Cluster hardening guide](https://microk8s.io/docs/how-to-cis-harden)|
|AWS EKS|[Best Practices for Security, Identity and Compliance](https://aws.amazon.com/architecture/security-identity-compliance), [AWS security credentials](https://docs.aws.amazon.com/IAM/latest/UserGuide/security-creds.html), [Security in EKS](https://docs.aws.amazon.com/eks/latest/userguide/security.html)|
|Azure AKS|[Azure security best practices and patterns](https://learn.microsoft.com/en-us/azure/security/fundamentals/best-practices-and-patterns), [Managed identities for Azure resource](https://learn.microsoft.com/en-us/entra/identity/managed-identities-azure-resources/), [Security in AKS](https://learn.microsoft.com/en-us/azure/aks/concepts-security)|
|GCP GKE|[Google security overview](https://cloud.google.com/kubernetes-engine/docs/concepts/security-overview), [Harden your cluster’s security](https://cloud.google.com/kubernetes-engine/docs/how-to/hardening-your-cluster)|

### Juju

Juju is the component responsible for orchestrating the entire lifecycle, from deployment to Day 2 operations. For more information on Juju security hardening, see the [Juju security page](https://documentation.ubuntu.com/juju/latest/explanation/juju-security/index.html) and the [How to harden your deployment](https://documentation.ubuntu.com/juju/3.6/howto/manage-your-juju-deployment/harden-your-juju-deployment/#harden-your-deployment) guide.

#### Cloud credentials

When configuring cloud credentials to be used with Juju, ensure that users have the correct permissions to operate at the required level on the Kubernetes cluster. Juju superusers responsible for bootstrapping and managing controllers require elevated permissions to manage several kinds of resources. For this reason, the K8s user for bootstrapping and managing the deployments should have full permissions, such as:

* create, delete, patch, and list:
  * namespaces
  * services
  * deployments
  * stateful sets
  * pods
  * PVCs

In general, it is common practice to run Juju using the admin role of K8s, to have full permissions on the Kubernetes cluster.

#### Juju users

It is very important that Juju users are set up with minimal permissions depending on the scope of their operations. Please refer to the [User access levels](https://juju.is/docs/juju/user-permissions) documentation for more information on the access levels and corresponding abilities.

Juju user credentials must be stored securely and rotated regularly to limit the chances of unauthorised access due to credentials leakage.

## Applications

In the following sections, we provide guidance on how to harden your deployment using:

1. Base images
2. Charmed operator security upgrades
3. Encryption
4. Authentication
5. Monitoring and auditing

### Base images

Charmed PostgreSQL K8s and Charmed PgBouncer K8s run on top of rockcraft-based images shipping the PostgreSQL and PgBouncer distribution binaries built by Canonical. These images (rocks) are available in a GitHub registry for [PostgreSQL](https://github.com/canonical/charmed-postgresql-rock/pkgs/container/charmed-postgresql) and [PgBouncer](https://github.com/orgs/canonical/packages/container/package/charmed-pgbouncer) respectively. Both images are based on Ubuntu 22.04.

### Charmed operator security upgrades

[Charmed PostgreSQL K8s](https://charmhub.io/postgresql-k8s) operator and [Charmed PgBouncer K8s](https://charmhub.io/pgbouncer-k8s) operator install pinned versions of their respective rocks to provide reproducible and secure environments.

New versions (revisions) of the charmed operators can be released to update the operator's code, workloads, or both. It is important to refresh the charms regularly to make sure the workloads are as secure as possible.

For more information on upgrading Charmed PostgreSQL K8s, see the [How to upgrade PostgreSQL K8s](https://canonical.com/data/docs/postgresql/k8s/h-upgrade) and [How to upgrade PgBouncer K8s](https://charmhub.io/pgbouncer-k8s/docs/h-upgrade) guides, as well as the respective Release notes for [PostgreSQL](https://canonical.com/data/docs/postgresql/k8s/r-releases) and [PgBouncer](https://charmhub.io/pgbouncer-k8s/docs/r-releases).

### Encryption

To utilise encryption at transit for all internal and external cluster connections, integrate Charmed PostgreSQL K8s and Charmed PgBouncer K8s with a TLS certificate provider. Please refer to the [Charming Security page](https://charmhub.io/topics/security-with-x-509-certificates) for more information on how to select the right certificate provider for your use case.

Encryption in transit for backups is provided by the storage service (Charmed PostgreSQL K8s is a client for an S3-compatible storage).

For more information on encryption, see [](/explanation/security/cryptography) and [](/how-to/enable-tls).

### Authentication

Charmed PostgreSQL K8s supports the password-based `scram-sha-256` authentication method for authentication between:

* External connections to clients
* Internal connections between members of cluster
* PgBouncer connections

For more implementation details, see the [PostgreSQL documentation](https://www.postgresql.org/docs/14/auth-password.html).

### Monitoring and auditing

Charmed PostgreSQL K8s provides native integration with the [Canonical Observability Stack (COS)](https://charmhub.io/topics/canonical-observability-stack). To reduce the blast radius of infrastructure disruptions, the general recommendation is to deploy COS and the observed application into separate environments, isolated from one another. Refer to the [COS production deployments best practices](https://charmhub.io/topics/canonical-observability-stack/reference/best-practices) for more information or see the How to guides for PostgreSQL [monitoring](https://canonical.com/data/docs/postgresql/k8s/h-enable-monitoring), [alert rules](https://canonical.com/data/docs/postgresql/k8s/h-enable-alert-rules), and [tracing](https://canonical.com/data/docs/postgresql/k8s/h-enable-tracing) for practical instructions.

PostgreSQL logs are stored in `/var/log/postgresql` within the postgresql container of each unit. It’s recommended to integrate the charm with [COS](https://canonical.com/data/docs/postgresql/k8s/h-enable-monitoring), from where the logs can be easily persisted and queried using [Loki](https://charmhub.io/loki-k8s)/[Grafana](https://charmhub.io/grafana).

### Security event logging

Charmed PostgreSQL K8s provides [PostgreSQL Audit Extension (or pgAudit)](https://www.pgaudit.org/) enabled by default. These logs are stored in the `/var/log/postgresql/` directory of each unit along with the regular workload logs, and rotated minutely. If COS is enabled, audit logs are also persisted there.

The following information is configured to be logged:

* Statements related to roles and privileges, such as GRANT, REVOKE, CREATE, ALTER, and DROP ROLE.
* Data Definition Language (DDL) statements.
* Miscellaneous commands like DISCARD, FETCH, CHECKPOINT, VACUUM, SET.
* Miscellaneous SET commands.

Other events, like connections and disconnections, are logged depending on the value of the charm configuration options related to them. For more information, check the configuration options with the `logging` prefix in the [configuration reference](https://charmhub.io/postgresql-k8s/configurations#logging_log_connections).

Also, all operations performed by the charm as a result of user actions — such as enabling or disabling plugins, managing TLS, creating or restoring backups, and configuring replication between clusters (asynchronous or logical) — are executed through the underlying workload components (PostgreSQL, Patroni, or pgBackRest). Consequently, these operations are recorded in the respective workload log files, which are accessible in the `/var/log/postgresql` directory and also forwarded to COS.

No secrets are logged.

## Additional Resources

For details on the cryptography used by Charmed PostgreSQL K8s, see the [Cryptography](/explanation/security/cryptography) explanation page.


```{toctree}
:titlesonly:
:maxdepth: 2
:glob:
:hidden:

Cryptography <cryptography>