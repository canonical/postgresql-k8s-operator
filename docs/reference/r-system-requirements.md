# System Requirements

The following are the minimum software and hardware requirements to run Charmed PostgreSQL on K8s.

## Software
* Ubuntu 22.04 LTS (Jammy) or later.

### Juju

The charm supports several Juju releases from [2.9 LTS](https://juju.is/docs/juju/roadmap#juju-juju-29) onwards. The table below shows which minor versions of each major Juju release are supported by Charmed PostgreSQL.

| Juju major release | Supported minor versions | Compatible charm revisions |Comment |
|:--------|:-----|:-----|:-----|
| ![3.6 LTS] | `3.6.1+` | [444/445]+ | Recommended for production. |
| [![3.5]](https://juju.is/docs/juju/roadmap#juju-juju-35) | `3.5.1+` | [280]+  | [Known Juju issue](https://bugs.launchpad.net/juju/+bug/2066517) in `3.5.0` |
| [![3.4]](https://juju.is/docs/juju/roadmap#juju-juju-34) | `3.4.3+` | [280]+  | Know Juju issues with previous minor versions |
| [![3.3]](https://juju.is/docs/juju/roadmap#juju-juju-33) | `3.3.0+` | from [177] to [193]  | No known issues |
| [![3.2]](https://juju.is/docs/juju/roadmap#juju-juju-32) | `3.2.0+` | from [177] to [193] | No known issues |
| [![3.1]](https://juju.is/docs/juju/roadmap#juju-juju-31) | `3.1.7+` | from [177] to [193]| Juju secrets were stabilized in `3.1.7` |
| [![2.9 LTS]](https://juju.is/docs/juju/roadmap#juju-juju-29) | `2.9.49+` | [73]+ |
|  | `2.9.32+` | [73] to [193] | No tests for older Juju versions. |

### Kubernetes

* Kubernetes `1.27+`
* Canonical MicroK8s `1.27+` (snap channel `1.27-strict/stable` and newer)

## Hardware

- 8GB of RAM.
- 2 CPU threads.
- At least 20GB of available storage.
- Access to the internet for downloading the required OCI/ROCKs and charms.

### Supported architectures
The charm is based on the [charmed-postgresql snap](https://snapcraft.io/charmed-postgresql). It currently supports:
* `amd64`
* `arm64` (from revision `211+`)

The charm is based on the [ROCK OCI](https://github.com/canonical/charmed-postgresql-rock) named [`charmed-postgresql`](https://github.com/canonical/charmed-postgresql-rock/pkgs/container/charmed-postgresql).

[Contact us](/t/11852) if you are interested in new architectures!

## Networking

At the moment IPv4 is supported only (see more [info](https://warthogs.atlassian.net/browse/DPE-4695)).

[Contact us](/t/11852) if you are interested in IPv6!

<!-- BADGES -->

[2.9 LTS]: https://img.shields.io/badge/2.9_LTS-%23E95420?label=Juju
[3.1]: https://img.shields.io/badge/3.1-%23E95420?label=Juju
[3.2]: https://img.shields.io/badge/3.2-%23E95420?label=Juju
[3.3]: https://img.shields.io/badge/3.3-%23E95420?label=Juju
[3.4]: https://img.shields.io/badge/3.4-%23E95420?label=Juju
[3.5]: https://img.shields.io/badge/3.5-%23E95420?label=Juju
[3.6 LTS]: https://img.shields.io/badge/3.6_LTS-%23E95420?label=Juju

<!-- LINKS -->
[73]: /t/11873
[177]: /t/12668
[193]: /t/13208
[280]: /t/14068
[444/445]: /t/15966