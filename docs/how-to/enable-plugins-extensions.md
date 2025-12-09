# How to enable plugins/extensions

This guide shows how to enable a plugin/extension for an application charm that you want to integrate to Charmed PostgreSQL.


## Enable plugin/extension

Enable the plugin/extension by setting `True` as the value of its respective config option, like in the following example:

```text
juju config postgresql-k8s plugin-<plugin name>-enable=True
```

## Integrate your application

```text
juju integrate <application charm> postgresql-k8s 
```

If your application charm requests extensions through `db` or `db-admin` relation data, but the extension is not enabled yet, you'll see that the PostgreSQL application goes into a blocked state with the following message:
```text
postgresql-k8s/0*  blocked   idle   10.1.123.30            extensions requested through relation
```

In the [Juju debug logs](https://juju.is/docs/juju/juju-debug-log) we can see the list of extensions that need to be enabled:

```text
unit-postgresql-k8s-0: 18:04:51 ERROR unit.postgresql-k8s/0.juju-log db:5: ERROR - `extensions` (pg_trgm, unaccent) cannot be requested through relations - Please enable extensions through `juju config` and add the relation again.
```

After enabling the needed extensions through the config options, the charm will unblock. If you have removed the relation, you can add it back again.

If the application charm uses the new `postgresql_client` interface, it can use the [is_postgresql_plugin_enabled](https://charmhub.io/data-platform-libs/libraries/data_interfaces) helper method from the data interfaces library to check whether the plugin/extension is already enabled in the database.

```{note}
Not all PostgreSQL extensions are available. The list of supported extensions is available [](/reference/plugins-extensions).
```

