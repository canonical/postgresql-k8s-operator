# System requirements

The following are the minimum software and hardware requirements to run Charmed PostgreSQL on K8s.

## Software
* Ubuntu 22.04 LTS (Jammy) or later.

### Juju

The charm supports several Juju releases, starting from [2.9 LTS](https://documentation.ubuntu.com/juju/3.6/releasenotes/juju_2.9.x/#juju-2-9-0). The table below shows which minor versions of each major Juju release are supported by Charmed PostgreSQL.

| Juju major release | Supported minor versions | Compatible charm revisions |Comment |
|:--------|:-----|:-----|:-----|
| ![3.6 LTS] | `3.6.1+` | [444/445]+ | Recommended for production. |
| [![3.5]](https://documentation.ubuntu.com/juju/3.6/releasenotes/unsupported/juju_3.x.x/#juju-3-5) | `3.5.1+` | [280]+  | [Known Juju issue](https://bugs.launchpad.net/juju/+bug/2066517) in `3.5.0` |
| [![3.4]](https://documentation.ubuntu.com/juju/3.6/releasenotes/unsupported/juju_3.x.x/#juju-3-4) | `3.4.3+` | [280]+  | Know Juju issues with previous minor versions |
| [![3.3]](https://documentation.ubuntu.com/juju/3.6/releasenotes/unsupported/juju_3.x.x/#juju-3-3) | `3.3.0+` | from [177] to [193]  | No known issues |
| [![3.2]](https://documentation.ubuntu.com/juju/3.6/releasenotes/unsupported/juju_3.x.x/#juju-3-2) | `3.2.0+` | from [177] to [193] | No known issues |
| [![3.1]](https://documentation.ubuntu.com/juju/3.6/releasenotes/unsupported/juju_3.x.x/#juju-3-1) | `3.1.7+` | from [177] to [193]| Juju secrets were stabilised in `3.1.7` |
| [![2.9 LTS]](https://documentation.ubuntu.com/juju/3.6/releasenotes/juju_2.9.x/#) | `2.9.49+` | [73]+ |
|  | `2.9.32+` | [73] to [193] | No tests for older Juju versions. |

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

[2.9 LTS]: https://img.shields.io/badge/2.9_LTS-%23E95420?label=Juju
[3.1]: https://img.shields.io/badge/3.1-%23E95420?label=Juju
[3.2]: https://img.shields.io/badge/3.2-%23E95420?label=Juju
[3.3]: https://img.shields.io/badge/3.3-%23E95420?label=Juju
[3.4]: https://img.shields.io/badge/3.4-%23E95420?label=Juju
[3.5]: https://img.shields.io/badge/3.5-%23E95420?label=Juju
[3.6 LTS]: https://img.shields.io/badge/3.6_LTS-%23E95420?label=Juju

<!-- LINKS -->
[73]: https://github.com/canonical/postgresql-k8s-operator/releases/tag/rev73
[177]: https://github.com/canonical/postgresql-k8s-operator/releases/tag/rev177
[193]: https://github.com/canonical/postgresql-k8s-operator/releases/tag/rev193
[280]: https://github.com/canonical/postgresql-k8s-operator/releases/tag/rev280
[444/445]: https://github.com/canonical/postgresql-k8s-operator/releases/tag/rev444

