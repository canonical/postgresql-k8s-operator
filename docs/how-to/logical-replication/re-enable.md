# How to re-enable logical replication

If the relation between the PostgreSQL clusters is broken, you can re-enable logical replication by following these steps.

Drop and re-create the table on the second cluster:
```sh
psql postgresql://relation_id_9:nfWkAiEtSA3iA7t2@postgresql2-primary.dev.svc.cluster.local:5432/testdb
psql (16.9 (Ubuntu 16.9-0ubuntu0.24.04.1))
Type "help" for help.

testdb=> drop table asd; create table asd (message int);
DROP TABLE
CREATE TABLE
```

If the table is not dropped and re-created, the second cluster will get into a blocked state like in the following example:
```sh
# Juju status.
postgresql2/0*  blocked   idle  10.1.176.92         Logical replication setup is invalid. Check logs

# Juju debug logs.
unit-postgresql2-0: 15:42:34 ERROR unit.postgresql2/0.juju-log Logical replication validation: table public.asd in database testdb isn't empty
```

Then, integrate the clusters again:
```sh
juju integrate postgresql1:logical-replication-offer postgresql2:logical-replication
```

And you'll be able to see the data replicated from the first cluster to the second:
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
