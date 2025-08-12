# How to set up clusters for logical replication

```{caution}
This feature is only available for revision 630 or higher, which is not yet in the stable track.
```

Start by deploying two PostgreSQL clusters:
```sh
juju deploy postgresql-k8s --channel 16/edge --trust postgresql1
juju deploy postgresql-k8s --channel 16/edge --trust postgresql2
```

For testing purposes, you can deploy two applications of the [data integrator charm](https://charmhub.io/data-integrator) and then integrate them to the two PostgreSQL clusters you want to replicate data between.
```sh
juju deploy data-integrator di1 --config database-name=testdb
juju deploy data-integrator di2 --config database-name=testdb

juju integrate postgresql1 di1
juju integrate postgresql2 di2
```

Then, integrate both PostgreSQL clusters:
```sh
juju integrate postgresql1:logical-replication-offer postgresql2:logical-replication
```

This will create a publication on the first cluster and a subscription on the second cluster, allowing data to be replicated from the first to the second.

Request the credentials for the first PostgreSQL cluster.
```sh
juju run di1/leader get-credentials
```

The output example:
```yaml
postgresql:
  data: '{"database": "testdb", "external-node-connectivity": "true", "provided-secrets":
    "[\"mtls-cert\"]", "requested-secrets": "[\"username\", \"password\", \"tls\",
    \"tls-ca\", \"uris\", \"read-only-uris\"]"}'
  database: testdb
  endpoints: postgresql1-primary.dev.svc.cluster.local:5432
  password: NTgtJkVfUHLiYDk5
  read-only-endpoints: postgresql1-primary.dev.svc.cluster.local:5432
  read-only-uris: postgresql://relation_id_8:NTgtJkVfUHLiYDk5@postgresql1-primary.dev.svc.cluster.local:5432/testdb
  tls: "False"
  tls-ca: ""
  uris: postgresql://relation_id_8:NTgtJkVfUHLiYDk5@postgresql1-primary.dev.svc.cluster.local:5432/testdb
  username: relation_id_8
  version: "16.9"
```

Then create a table and insert some data into it on the first cluster:
```sh
psql postgresql://relation_id_8:NTgtJkVfUHLiYDk5@postgresql1-primary.dev.svc.cluster.local:5432/testdb
psql (16.9 (Ubuntu 16.9-0ubuntu0.24.04.1))
Type "help" for help.

testdb=> create table asd (message int); insert into asd values (123);
CREATE TABLE
INSERT 0 1
```

After that, you need to create the same table on the second cluster so that the data can be replicated. Start by getting the credentials for the second cluster:
```sh
juju run di2/leader get-credentials
```

The output example:
```yaml
postgresql:
  data: '{"database": "testdb", "external-node-connectivity": "true", "provided-secrets":
    "[\"mtls-cert\"]", "requested-secrets": "[\"username\", \"password\", \"tls\",
    \"tls-ca\", \"uris\", \"read-only-uris\"]"}'
  database: testdb
  endpoints: postgresql2-primary.dev.svc.cluster.local:5432
  password: nfWkAiEtSA3iA7t2
  read-only-endpoints: postgresql2-primary.dev.svc.cluster.local:5432
  read-only-uris: postgresql://relation_id_9:nfWkAiEtSA3iA7t2@postgresql2-primary.dev.svc.cluster.local:5432/testdb
  tls: "False"
  tls-ca: ""
  uris: postgresql://relation_id_9:nfWkAiEtSA3iA7t2@postgresql2-primary.dev.svc.cluster.local:5432/testdb
  username: relation_id_9
  version: "16.9"
```

Then create the same table on the second cluster:
```sh
psql postgresql://relation_id_9:nfWkAiEtSA3iA7t2@postgresql2-primary.dev.svc.cluster.local:5432/testdb
psql (16.9 (Ubuntu 16.9-0ubuntu0.24.04.1))
Type "help" for help.

testdb=> create table asd (message int);
CREATE TABLE
```

Configure the replication of that specific database and table (remember to specify the table schema; it's the `public` schema in this example):
```sh
juju config postgresql2 logical_replication_subscription_request='{"testdb": ["public.asd"]}'
```

After a few seconds, you can check that the data has been replicated:
```sh
psql postgresql://relation_id_9:nfWkAiEtSA3iA7t2@postgresql2-primary.dev.svc.cluster.local:5432/testdb
psql (16.9 (Ubuntu 16.9-0ubuntu0.24.04.1))
Type "help" for help.

testdb=> select * from asd;
 message
---------
     123
(1 row)
```

You can then add more data to the table in the first cluster, and it will be replicated to the second cluster automatically.

It's also possible to replicate tables in the other direction, from the second cluster to the first, while keeping the replication from the first cluster to the second. To do that, you need to also integrate the clusters in the opposite direction:
```sh
psql postgresql://relation_id_9:nfWkAiEtSA3iA7t2@postgresql2-primary.dev.svc.cluster.local:5432/testdb
psql (16.9 (Ubuntu 16.9-0ubuntu0.24.04.1))
Type "help" for help.

testdb=> create table asd2 (message int); insert into asd2 values (123);
CREATE TABLE
INSERT 0 1
testdb=> \q

psql postgresql://relation_id_8:NTgtJkVfUHLiYDk5@postgresql1-primary.dev.svc.cluster.local:5432/testdb
psql (16.9 (Ubuntu 16.9-0ubuntu0.24.04.1))
Type "help" for help.

testdb=> create table asd2 (message int);
CREATE TABLE
testdb=> \q

juju integrate postgresql1:logical-replication postgresql2:logical-replication-offer

juju config postgresql1 logical_replication_subscription_request='{"testdb": ["public.asd2"]}'

psql postgresql://relation_id_8:NTgtJkVfUHLiYDk5@postgresql1-primary.dev.svc.cluster.local:5432/testdb
psql (16.9 (Ubuntu 16.9-0ubuntu0.24.04.1))
Type "help" for help.

testdb=> select * from asd2;
 message
---------
     123
(1 row)
```

And the same table, or even different tables, can be replicated to multiple clusters at the same time. For example, you can replicate the `asd` table from the first cluster to both a second and a third clusters, or you can replicate it only to the second cluster and replicate a different table to the third cluster.

If the relation between the PostgreSQL clusters is broken, the data will be kept in both clusters, but the replication will stop. You can re-enable logical replication by following the steps from [](/how-to/logical-replication/re-enable).

The same will happen for that specific table if you change the table in the `logical_replication_subscription_request` config option to a different table or remove it completely. If one or more tables other than the current one are specified, the replication will continue for those tables, but the current table will not be replicated any more.
