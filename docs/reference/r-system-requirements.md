# System Requirements

The following are the minimum software and hardware requirements to run Charmed PostgreSQL on K8s.

## Software
* Ubuntu 22.04 (Jammy) or later.

### Juju

The charm supports both [Juju 2.9 LTS](https://github.com/juju/juju/releases) and [Juju 3.1](https://github.com/juju/juju/releases).

The minimum supported Juju versions are:

* 2.9.32+ (no tests made for older versions).
* 3.1.7+ (Juju secrets refactored/stabilized in Juju 3.1.7)

[note type="caution"]
**Note**: Juju 3.1 is supported from the charm revision 116+
[/note]

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

[Contact us](https://chat.charmhub.io/charmhub/channels/data-platform) if you are interested in new architecture!