# How to enable plugins/extensions

[note]
**Note:** This feature is currently only available in the channel `14/edge` (revision 103+) and will be released to the channel `14/stable` soon.
[/note]

This guide shows how to enable a plugin/extension for an application charm that you want to integrate to Charmed PostgreSQL.

## Prerequisites
* A deployed [Charmed PostgreSQL K8s operator](/t/charmed-postgresql-k8s-tutorial-deploy/9298?channel=14)

## Enable plugin/extension
Enable the plugin/extension by setting `True` as the value of its respective config option, like in the following example:

```shell
juju config postgresql-k8s plugin_<plugin name>_enable=True
```
## Integrate your application
Integrate (formerly known as "relate" in `juju v.2.9`) your application charm with the PostgreSQL charm:

```shell
juju integrate <application charm> postgresql-k8s 
```

If your application charm requests extensions through `db` or `db-admin` relation data, but the extension is not enabled yet, you'll see that the PostgreSQL application goes into a blocked state with the following message:
```shell
postgresql-k8s/0*  blocked   idle   10.1.123.30            extensions requested through relation
```

In the [Juju debug logs](https://juju.is/docs/juju/juju-debug-log) we can see the list of extensions that need to be enabled:

```shell
unit-postgresql-k8s-0: 18:04:51 ERROR unit.postgresql-k8s/0.juju-log db:5: ERROR - `extensions` (pg_trgm, unaccent) cannot be requested through relations - Please enable extensions through `juju config` and add the relation again.
```

After enabling the needed extensions through the config options, the charm will unblock. If you have removed the relation, you can add it back again.

If the application charm uses the new `postgresql_client` interface, it can use the [is_postgresql_plugin_enabled](https://charmhub.io/data-platform-libs/libraries/data_interfaces#databaserequires-is_postgresql_plugin_enabled) helper method from the data interfaces library to check whether the plugin/extension is already enabled in the database.

[note]
**Note:** Not all PostgreSQL extensions are available. The list of supported extensions is available at [ Supported plugins/extensions](/t/charmed-postgresql-k8s-reference-supported-plugins-extensions/10945).
[/note]