# Deploy in an offline or air-gapped environment

An air-gapped environment refers to a system that does not have access to the public internet.
This guide goes through the special configuration steps for installing Charmed PostgreSQL k8s in an air-gapped environment.

## Requirements

Canonical does not prescribe how you should set up your specific air-gapped environment. However, it is assumed that it meets the following conditions:

* A K8s cluster is running.
* DNS is configured to the local nameservers.
* [Juju is configured](https://documentation.ubuntu.com/snap-store-proxy/en/airgap-charmhub/#configure-juju) to use local air-gapped services.
* The [`store-admin`](https://snapcraft.io/store-admin) tool is installed and configured.
* [Air-gapped CharmHub](https://documentation.ubuntu.com/snap-store-proxy/en/airgap-charmhub/) is installed and running.
* Local APT and LXD Images caches are reachable.
* An air-gapped container registry (such as [Artifactory](https://jfrog.com/artifactory/)) is reachable from the K8s cluster over HTTPS
  *  **Note**: Secure (HTTPS) OCI access is important, otherwise Juju won’t work!

## Air-gapped concept summary

1. [Export](https://documentation.ubuntu.com/snap-store-proxy/en/airgap-charmhub/#export-packages)
2. [Transfer](https://en.wikipedia.org/wiki/Air_gap_(networking))
3. [Import](https://documentation.ubuntu.com/snap-store-proxy/en/airgap-charmhub/#import-packages)
4. [Deploy](/t/9298) 

## Air-gapped day-to-day example

**1.** Exporting K8s Charms and OCI Resources are currently independent processes.
> Sseveral improvements are planned:  [#1](https://warthogs.atlassian.net/browse/PF-5369), [#2](https://warthogs.atlassian.net/browse/PF-5185)

**1.1.** Charm. The necessary charm(s) can be exported as bundle OR independently (charm-by-charm). The special store-admin tool is designed to simplify the process. At the moment exporting of Charms and OCI resources are separated, but in the future the `store-admin export` [could](https://documentation.ubuntu.com/snap-store-proxy/en/airgap-charmhub/#export-charms) export all necessary OCI resource(s)) from official CharmHub.

At the moment, the store-admin exports (and includes into the blob) all the OCI resources metadata only:
```shell
store-admin export bundle postgresql-k8s-bundle --channel=14/edge --series=jammy --arch=amd64
```

[details="Example output"]

```shell
> store-admin export bundle postgresql-k8s-bundle --channel=14/edge --series=jammy --arch=amd64
Downloading postgresql-k8s-bundle revision 141 (14/edge)
[####################################]  100%
Downloading data-integrator revision 71 (edge)
[####################################]  100%
Downloading grafana-agent-k8s revision 93 (edge)
[####################################]  100%
Downloading resources for grafana-agent-k8s
Downloading oci-image resource agent-image revision 45
[####################################]  100%
Falling back to OCI image subpath from online Charmhub for 'agent-image' in charm 'grafana-agent-k8s'.
Downloading pgbouncer-k8s revision 301 (1/edge)
[####################################]  100%
Downloading resources for pgbouncer-k8s
Downloading oci-image resource pgbouncer-image revision 85
[####################################]  100%
Falling back to OCI image subpath from online Charmhub for 'pgbouncer-image' in charm 'pgbouncer-k8s'.
Downloading postgresql-k8s revision 406 (14/edge)
[####################################]  100%
Downloading resources for postgresql-k8s
Downloading oci-image resource postgresql-image revision 164
[####################################]  100%
Falling back to OCI image subpath from online Charmhub for 'postgresql-image' in charm 'postgresql-k8s'.
Downloading postgresql-test-app revision 254 (edge)
[####################################]  100%
Downloading s3-integrator revision 59 (edge)
[####################################]  100%
Downloading self-signed-certificates revision 200 (edge)
[####################################]  100%
Downloading sysbench revision 78 (edge)
[####################################]  100%
Successfully exported charm bundle postgresql-k8s-bundle: /home/ubuntu/snap/store-admin/common/export/postgresql-k8s-bundle-20241003T104903.tar.gz
```

[/details]

**1.2.** OCI: for the manual OCI export, please follow [the official CharmHub guide](https://documentation.ubuntu.com/snap-store-proxy/en/airgap-charmhub/#export-oci-images).

**2.** Transfer the binary blobs using the way of your choice into Air-gapped environment.

```shell
cp /home/ubuntu/snap/store-admin/common/export/postgresql-k8s-bundle-20241003T104903.tar.gz /media/usb/
...
cp /media/usb/postgresql-k8s-bundle-20241003T104903.tar.gz /var/snap/snap-store-proxy/common/charms-to-push/
```
> **Note**: always check [checksum](https://en.wikipedia.org/wiki/Checksum) for the transferred blobs!

**3.** Upload the charm blobs into local Air-gapped CharmHub:
```shell
sudo snap-store-proxy push-charm-bundle /var/snap/snap-store-proxy/common/charms-to-push/postgresql-k8s-bundle-20241003T104903.tar.gz
```
> **Note**: when [re-importing](https://documentation.ubuntu.com/snap-store-proxy/en/airgap-charmhub/#import-packages) charms or importing other revisions, make sure to provide the `--push-channel-map`.

**4.** Upload the charm OCI into local Air-gapped OCI registry.

For the manual OCI import, please follow [the official CharmHub guide](https://documentation.ubuntu.com/snap-store-proxy/en/airgap-charmhub/#import-packages).

**5.** [Deploy and enjoy Juju charms the usual way](/t/9298):
```shell
juju deploy postgresql-k8s --trust
```
> **Note**: all the Air-gapp-deployed charms revisions and OCI resources tags/revisions must match the official CharmHub revisions/tags (users can rely in [the official release notes](/t/11872)).

## Additional links:

* https://docs.ubuntu.com/snap-store-proxy/en/airgap
* https://documentation.ubuntu.com/snap-store-proxy/
* https://documentation.ubuntu.com/snap-store-proxy/en/airgap-charmhub/
* https://ubuntu.com/kubernetes/docs/install-offline
* https://charmed-kubeflow.io/docs/install-in-airgapped-environment