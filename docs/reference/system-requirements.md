(system-requirements)=
# System requirements

The following are the minimum software and hardware requirements to run Charmed PostgreSQL on K8s.

## Software
* Ubuntu 24.04 (Noble) or later.
  
### Juju

Charmed PostgreSQL 16 supports several Juju releases from 3.6 LTS onwards. The table below shows which minor versions of each major Juju release are supported by the stable Charmhub releases of PostgreSQL.

| Juju major release | Supported minor versions | Compatible charm revisions |Comment |
|:--------|:-----|:-----|:-----|
| ![3.6 LTS] | `3.6.1+` |  | |

### Kubernetes

* Kubernetes `1.27+`
* Canonical MicroK8s `1.27+` (snap channel `1.27-strict/stable` and newer)

## Hardware

- 8GB of RAM.
- 2 CPU threads.
- At least 20GB of available storage.
- Access to the internet for downloading the required OCI/rocks and charms.

### Supported architectures
The charm is based on the [charmed-postgresql snap](https://snapcraft.io/charmed-postgresql). It currently supports:
* `amd64`
* `arm64` (from revision `211+`)

The charm is based on the [OCI/rock](https://github.com/canonical/charmed-postgresql-rock) named [`charmed-postgresql`](https://github.com/canonical/charmed-postgresql-rock/pkgs/container/charmed-postgresql).

[Contact us](/reference/contacts) if you are interested in new architectures!

## Networking

* Access to the internet is required for downloading required snaps and charms
* Only IPv4 is supported at the moment
  * See more information about this limitation in [this Jira issue](https://warthogs.atlassian.net/browse/DPE-4695)
  * [Contact us](/reference/contacts) if you are interested in IPv6!

<!-- BADGES -->

[3.6 LTS]: https://img.shields.io/badge/3.6_LTS-%23E95420?label=Juju

<!-- LINKS -->
[73]: https://github.com/canonical/postgresql-k8s-operator/releases/tag/73
[177]: https://github.com/canonical/postgresql-k8s-operator/releases/tag/177
[193]: https://github.com/canonical/postgresql-k8s-operator/releases/tag/193
[280]: https://github.com/canonical/postgresql-k8s-operator/releases/tag/280
[444/445]: https://github.com/canonical/postgresql-k8s-operator/releases/tag/444

