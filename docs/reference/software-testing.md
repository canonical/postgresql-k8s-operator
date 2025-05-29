


```{note}
**Note**: All commands are written for `juju >= v.3.1`

If you're using `juju 2.9`, check the [`juju 3.0` Release Notes](https://juju.is/docs/juju/roadmap#juju-3-0-0---22-oct-2022).
```

# Software testing for charms

Most types of standard [software tests](https://en.wikipedia.org/wiki/Software_testing) are applicable to Charmed PostgreSQL.

This reference addresses the following types:

* [Smoke test](#smoke)
* [Unit tests](#unit)
* [Integration tests](#integration)
* [System test](#system)

---

<!--TODO: table with other test types
Smoke: 
[u]Complexity[/u]: trivial<br/>
[u]Speed[/u]: fast<br/>

Unit: ...
-->

## Smoke test
This type of test ensures that basic functionality works over a short amount of time.
### Steps
1. [Set up a `juju v.3.x` environment](/tutorial/1-set-up-environment)
2. Deploy database with test application
3. Start "continuous write" test

<details><summary>Example</summary>

```text
juju add-model smoke-test

juju deploy postgresql-k8s --trust --channel 14/edge
juju scale-application postgresql-k8s 3 # (optional)

juju deploy postgresql-test-app
juju integrate postgresql-test-app:first-database postgresql-k8s

# Start "continuous write" test:
juju run postgresql-test-app/leader start-continuous-writes

export user=operator
export pass=$(juju run postgresql-k8s/leader get-password username=${user} | yq '.. | select(. | has("password")).password')
export relname=first-database
export ip=$(juju show-unit postgresql-k8s/0 --endpoint database | yq '.. | select(. | has("public-address")).public-address')
export db=$(juju show-unit postgresql-k8s/0 --endpoint database | yq '.. | select(. | has("database")).database')
export relid=$(juju show-unit postgresql-k8s/0 --endpoint database | yq '.. | select(. | has("relation-id")).relation-id')
export query="select count(*) from continuous_writes"

watch -n1 -x juju run postgresql-test-app/leader run-sql dbname=${db} query="${query}" relation-id=${relid} relation-name=${relname}

# OR

watch -n1 -x juju ssh --container postgresql postgresql-k8s/leader "psql postgresql://${user}:${pass}@${ip}:5432/${db} -c \"${query}\""

# Watch that the counter is growing!
```
</details>

### Expected results
* `postgresql-test-app` continuously inserts records into the database received through the integration (the table `continuous_writes`).
* The counters (amount of records in table) are growing on all cluster members

### Tips
To stop the "continuous write" test, run
```text
juju run postgresql-test-app/leader stop-continuous-writes
```
To truncate the "continuous write" table (i.e. delete all records from database), run
```text
juju run postgresql-test-app/leader clear-continuous-writes
```

## Unit test
Check the [Contributing guide](https://github.com/canonical/postgresql-k8s-operator/blob/main/CONTRIBUTING.md#testing) on GitHub and follow `tox run -e unit` examples there.

## Integration test
Check the [Contributing guide](https://github.com/canonical/postgresql-k8s-operator/blob/main/CONTRIBUTING.md#testing) on GitHub and follow `tox run -e integration` examples there.

## System test
To perform a system test, deploy [`postgresql-k8s-bundle`](https://charmhub.io/postgresql-k8s-bundle). This charm bundle automatically deploys and tests all the necessary parts at once.

