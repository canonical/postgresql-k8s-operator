## Charm types "legacy" vs "modern"
There are [two types of charms](https://juju.is/docs/sdk/charm-taxonomy#heading--charm-types-by-generation) stored under the same charm name `postgresql-k8s`:

1. [Reactive](https://juju.is/docs/sdk/charm-taxonomy#heading--reactive)  charm in the channel `latest/stable` (called `legacy`)
2. [Ops-based](https://juju.is/docs/sdk/ops) charm in the channel `14/stable` (called `modern`)

The legacy charm provided endpoints `db` and `db-admin` (for the interface `pgsql`). The modern charm provides old endpoints as well + new endpoint `database` (for the interface `postgresql_client`). Read more details about the available [endpoints/interfaces](https://charmhub.io/postgresql-k8s/docs/e-interfaces).

**Note**: Please choose one endpoint to use. No need to relate all of them simultaneously!

## The default track "latest" vs "14"

The [default track](https://docs.openstack.org/charm-guide/yoga/project/charm-delivery.html) has been switched from the `latest` to `14` for both VM and K8s PostgreSQL charms. It is [to ensure](https://discourse.charmhub.io/t/request-switch-default-track-from-latest-to-14-for-postgresql-k8s-charms/10314) all new deployments use a modern codebase. We strongly advise against using the latest track due to its implicit nature. In doing so, a future charm upgrade may result in a PostgreSQL version incompatible with an integrated application. Track 14 guarantees PostgreSQL 14 deployment only. The track `latest` will be closed after all applications migrated from Reactive to Ops-based charm.

<a name="howtomigrate"></a>
## How to migrate from "legacy" to "modern" charm

The "modern" charm provides temporary support for the legacy interfaces:

* **quick try**: relate the current application with new charm using endpoint `db` (set the channel to `14/stable`). No extra changes necessary:

```
  postgresql:
    charm: postgresql-k8s
    channel: 14/stable
    trust: true
```

* **proper migration**: migrate the application to the new interface "[postgresql_client](https://github.com/canonical/charm-relation-interfaces)". The application will connect PostgreSQL using "[data_interfaces](https://charmhub.io/data-platform-libs/libraries/data_interfaces)" library from "[data-platform-libs](https://github.com/canonical/data-platform-libs/)" via endpoint `database`.

**Warning**: NO in-place upgrades possible! Reactive charm cannot be upgraded to Operator-framework-based one. To move DB data, the second/modern DB application must be launched nearby and data should be copied from "legacy" application to the "modern" one. Canonical Data Platform team is preparing copy&paste guide right now. Please [contact us](https://chat.charmhub.io/charmhub/channels/data-platform) if you need migration instructions.

**Note**: the `trust` option must be enabled if [ Role Based Access Control (RBAC)](https://kubernetes.io/docs/concepts/security/rbac-good-practices/) is in use on your Kubernetes.

## How to deploy old "legacy" postgresql charm

Deploy the charm using the channel `latest/stable`:

```
  postgresql:
    charm: postgresql-k8s
    channel: latest/stable
```

**Note**: remove Charm store prefix `cs:` from the bundle. Otherwise the modern charm will be chosen by Juju (due to the default track pointing to `14/stable` and not `latest/stable`). The common error message is: `cannot deploy application "postgresql": unknown option "..."`.

## Config options supported by modern charm

The legacy charm config options were not moved to the modern charm due to no need. The modern charm applies the best possible configuration automatically. Feel free to [contact us](https://chat.charmhub.io/charmhub/channels/data-platform) about the DB tuning/config options.

## Extensions supported by modern charm

The legacy charm provided plugins/extensions enabling through the relation (interface `pgsql`). It is NOT supported by the modern charm (neither `pgsql` nor `postgresql_client` interfaces). Please enable the necessary extensions using appropriate `plugin_*_enable` [config option](https://charmhub.io/postgresql-k8s/configure) of the modern charm. After enabling the modern charm will provide plugins support for both `pgsql` and `postgresql_client` interfaces.

Please find the list of supported PostgreSQL [Extensions](/t/10945) by modern charm. Feel free to [contact us](/t/11852) with a list of required extensions.

## Roles supported by modern charm

In the legacy charm, the user could request roles by setting the `roles` field to a comma separated list of desired roles. It is NOT supported by the modern charm implementation of the legacy `pgsql` interface. The same functionality is provided via the modern `postgresql_client` using "[extra-user-roles](/t/10843#extra-user-roles)". Please check how to [migrate on the new interface](/t/11853).

## Supported PostgreSQL versions by modern charm

At the moment, the modern charms support PostgreSQL 14 (based on Jammy/22.04 series) only.
Please [contact us](https://chat.charmhub.io/charmhub/channels/data-platform) if you need different versions/series.

## Supported architectures: amd64, arm64, ...
Currently, the charm supports architecture `amd64` (all revisions) and `arm64` (from revision 211+). 

See the technical details in [Supported architectures](/t/11744)

## How to report issues and contact authors

The "legacy charm" (from `latest/stable`) is stored on [Launchpad](https://git.launchpad.net/charm-k8s-postgresql), here is the link to report all [legacy charm issues](https://bugs.launchpad.net/charm-k8s-postgresql).

The "modern charm" (from `14/stable`) is stored on [GitHub](https://github.com/canonical/postgresql-k8s-operator), here is the link to report [modern charm issues](https://github.com/canonical/postgresql-k8s-operator/issues/new/choose).

Do you have questions? [Contact us](https://chat.charmhub.io/charmhub/channels/data-platform)!