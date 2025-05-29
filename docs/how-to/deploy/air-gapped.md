# Deploy in an offline or air-gapped environment

An air-gapped environment refers to a system that does not have access to the public internet.
This guide goes through the special configuration steps for installing Charmed PostgreSQL K8s in an air-gapped environment.

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

## Air-gapped setup summary

[1\. Export charms and resources](#1-export-charms-and-resources) <br>
[2\. Transfer binary blobs](#2-transfer-binary-blobs) <br>
[3\. Import charms and resources](3-import-charms-and-resources) <br>
[4\. Deploy PostgreSQL](#4-deploy-postgresql)

## Air-gapped day-to-day example

### 1. Export charms and resources
Exporting K8s Charms and OCI Resources are currently independent processes. The `store-admin` tool is designed to simplify the process. 

Future improvements are planned to the `store-admin` tool so that it could potentially export all necessary OCI resource(s) from the official CharmHub store. Other planned improvements include supporting the export of specific charm and resource revisions ([PF-5369](https://warthogs.atlassian.net/browse/PF-5369), [PF-5185](https://warthogs.atlassian.net/browse/PF-5185)).

#### Charms
 The necessary charm(s) can be exported as bundle or independently (charm-by-charm). See the Snap Proxy documentation:
* [Offline Charmhub configuration > Export charm bundle](https://documentation.ubuntu.com/snap-store-proxy/en/airgap-charmhub/#export-charm-bundles)
* [Offline Charmhub configuration > Export charms](https://documentation.ubuntu.com/snap-store-proxy/en/airgap-charmhub/#export-charms)

At the moment, the `store-admin` tool only exports and includes the OCI resources' metadata into the blob:

<details> 
<summary> <code>store-admin export bundle postgresql-k8s-bundle --channel=14/edge --series=jammy --arch=amd64</code></summary>

```
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
</details>

#### OCI images
For manual OCI exports, follow the official Snap Store Proxy documentation: [Offline Charmhub configuration > Export OCI images](https://documentation.ubuntu.com/snap-store-proxy/en/airgap-charmhub/#export-oci-images).

### 2. Transfer the binary blobs 

Transfer the binary blobs using the way of your choice into the air-gapped environment.

```text
cp /home/ubuntu/snap/store-admin/common/export/postgresql-k8s-bundle-20241003T104903.tar.gz /media/usb/
...
cp /media/usb/postgresql-k8s-bundle-20241003T104903.tar.gz /var/snap/snap-store-proxy/common/charms-to-push/
```
> **Note**: always check [checksum](https://en.wikipedia.org/wiki/Checksum) for the transferred blobs!

### 3. Import charms and resources

#### Charms
 Upload the charm blobs into local air-gapped CharmHub:
```text
sudo snap-store-proxy push-charm-bundle /var/snap/snap-store-proxy/common/charms-to-push/postgresql-k8s-bundle-20241003T104903.tar.gz
```
> **Note**: when [re-importing](https://documentation.ubuntu.com/snap-store-proxy/en/airgap-charmhub/#import-packages) charms or importing other revisions, make sure to provide the `--push-channel-map`.

#### OCI resources

Upload the charm OCI into local Air-gapped OCI registry.

For the manual OCI import, please follow [the official CharmHub guide](https://documentation.ubuntu.com/snap-store-proxy/en/airgap-charmhub/#import-packages).

> For more details about exporting charms and resources, see:
>
> [Snap Store Proxy documentation > Offline Charmhub configuration > Import packages](https://documentation.ubuntu.com/snap-store-proxy/en/airgap-charmhub/#import-packages)

### 4. Deploy PostgreSQL

 Deploy and operate Juju charms normally:
```text
juju deploy postgresql-k8s --trust
```
```{note}
**Note**: All the charms revisions and OCI resources tags/revisions deployed in the air-gapped environment must match the official CharmHub revisions/tags. 

Use [the official release notes](/reference/releases) as a reference.
```

## Additional resources

* https://docs.ubuntu.com/snap-store-proxy/en/airgap
* https://documentation.ubuntu.com/snap-store-proxy/
* https://documentation.ubuntu.com/snap-store-proxy/en/airgap-charmhub/
* https://ubuntu.com/kubernetes/docs/install-offline
* [Charmed Kubeflow > Install in an airgapped environment](https://charmed-kubeflow.io/docs/install-in-airgapped-environment)
*  [Wikipedia > Air gap (networking)](https://en.wikipedia.org/wiki/Air_gap_(networking))

