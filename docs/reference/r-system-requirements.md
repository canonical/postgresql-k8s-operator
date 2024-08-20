# System Requirements

The following are the minimum software and hardware requirements to run Charmed PostgreSQL on K8s.

## Software
* Ubuntu 22.04 (Jammy) or later.

### Juju

The charm supports both [Juju 2.9 LTS](https://github.com/juju/juju/releases) and [Juju 3.x](https://github.com/juju/juju/releases) with the following conditions:

| Juju Releases | Juju Supported | Min charm revision |Comment |
|:--------|:-----|:-----|:-----|
| 2.9 LTS | 2.9.32+ | 73+ | no tests made for older versions |
| 3.1 | 3.1.7+ | 193+ | Juju secrets refactored/stabilized in Juju 3.1.7 |
|  3.2 | 3.2.0+ | 280+ | no known issues |
|  3.3 | 3.3.0+ | 280+ | no known issues |
|  3.4 | 3.4.3+ | 280+  | know Juju issues with previous versions |
|  3.5 | 3.5.1+ | 280+  | [known](https://bugs.launchpad.net/juju/+bug/2066517) Juju issue in 3.5.0 |
|  3.6 LTS | 3.6.0-beta1 | 280+ |  LAB TESTS ONLY! No known issues. |

### Kubernetes

* Kubernetes 1.27+
* Canonical MicroK8s 1.27+ (snap channel 1.27-strict/stable and newer)

## Hardware

- 8GB of RAM.
- 2 CPU threads.
- At least 20GB of available storage.
- Access to the internet for downloading the required OCI/ROCKs and charms.

### Supported architectures
The charm is based on the [charmed-postgresql snap](https://snapcraft.io/charmed-postgresql). It currently supports:
* `amd64`
* `arm64` (from revision 211+)

The charm is based on the [ROCK OCI](https://github.com/canonical/charmed-postgresql-rock) named [`charmed-postgresql`](https://github.com/canonical/charmed-postgresql-rock/pkgs/container/charmed-postgresql).

[Contact us](/t/11852) if you are interested in new architecture!

## Networking

At the moment IPv4 is supported only (see more [info](https://warthogs.atlassian.net/browse/DPE-4695)).

[Contact us](/t/11852) if you are interested in IPv6!