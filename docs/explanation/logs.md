# Logs

This page summarises all log types in Charmed PostgreSQL to simplify troubleshooting.

For an overview of all charm components, see [](/explanation/architecture).

## Core logs

PostgreSQL and Patroni logs can be found in `/var/log/postgresql` within the `postgresql` container of each unit:

```text
> ls -alh /var/log/postgresql/
total 60K
drwxr-xr-x 1 postgres root     4.0K Oct 11 11:45 .
drwxr-xr-x 1 root     root     4.0K Aug 18 12:53 ..
-rw-r--r-- 1 postgres postgres   86 Oct 11 11:44 patroni.log
-rw-r--r-- 1 postgres postgres  516 Oct 11 11:44 patroni.log.1
-rw-r--r-- 1 postgres postgres  516 Oct 11 11:43 patroni.log.2
-rw-r--r-- 1 postgres postgres  516 Oct 11 11:42 patroni.log.3
-rw-r--r-- 1 postgres postgres  583 Oct 11 11:41 patroni.log.4
-rw-r--r-- 1 postgres postgres  593 Oct 11 11:41 patroni.log.5
-rw-r--r-- 1 postgres postgres  588 Oct 11 11:40 patroni.log.6
-rw-r--r-- 1 postgres postgres  558 Oct 11 11:40 patroni.log.7
-rw-r--r-- 1 postgres postgres  449 Oct 11 11:40 patroni.log.8
-rw------- 1 postgres postgres 4.4K Oct 11 11:40 postgresql-3_1140.log
-rw------- 1 postgres postgres  114 Oct 11 11:41 postgresql-3_1141.log
-rw------- 1 postgres postgres    0 Oct 11 11:42 postgresql-3_1142.log
-rw------- 1 postgres postgres    0 Oct 11 11:43 postgresql-3_1143.log
-rw------- 1 postgres postgres    0 Oct 11 11:44 postgresql-3_1144.log
-rw------- 1 postgres postgres    0 Oct 11 11:45 postgresql-3_1145.log
```

The PostgreSQL log naming convention  is `postgresql-<weekday>_<hour><minute>.log`. The log message format is `<date> <time> UTC [<pid>]: <connection details> <level>: <message>`. E.g:

```text
> cat /var/log/postgresql/postgresql-3_1140.log
2023-10-11 11:40:12 UTC [49]: user=,db=,app=,client=,line=3 LOG:  starting PostgreSQL 14.9 (Ubuntu 14.9-0ubuntu0.22.04.1) on x86_64-pc-linux-gnu, compiled by gcc (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0, 64-bit
2023-10-11 11:40:12 UTC [49]: user=,db=,app=,client=,line=4 LOG:  listening on IPv4 address "0.0.0.0", port 5432
2023-10-11 11:40:12 UTC [49]: user=,db=,app=,client=,line=5 LOG:  listening on Unix socket "/var/run/postgresql/.s.PGSQL.5432"
2023-10-11 11:40:12 UTC [54]: user=,db=,app=,client=,line=1 LOG:  database system was shut down at 2023-10-11 11:40:11 UTC
2023-10-11 11:40:12 UTC [55]: user=operator,db=postgres,app=[unknown],client=127.0.0.1,line=1 FATAL:  the database system is starting up
2023-10-11 11:40:12 UTC [49]: user=,db=,app=,client=,line=6 LOG:  database system is ready to accept connections
```

The Patroni log message format is `<date> <time> UTC [<pid>]: <level>: <message>`. E.g:

```text
> cat /var/log/postgresql/patroni.log.27
2023-10-11 11:40:09 UTC [15]: INFO: No PostgreSQL configuration items changed, nothing to reload. 
2023-10-11 11:40:09 UTC [15]: INFO: Lock owner: None; I am pg-0 
2023-10-11 11:40:10 UTC [15]: INFO: trying to bootstrap a new cluster 
2023-10-11 11:40:12 UTC [15]: INFO: postmaster pid=49 
2023-10-11 11:40:13 UTC [15]: INFO: establishing a new patroni connection to the postgres cluster 
2023-10-11 11:40:13 UTC [15]: INFO: running post_bootstrap 
```

All timestamps are in UTC.

## Optional logs

If S3 backups are enabled, Pgbackrest logs would be located in `/var/log/pgbackrest` in the `postgresql` container:
```text
> ls -alh /var/log/pgbackrest/
total 24K
drwxr-xr-x 1 postgres root     4.0K Oct 11 13:07 .
drwxr-xr-x 1 root     root     4.0K Aug 18 12:53 ..
-rw-r----- 1 postgres postgres 1.5K Oct 11 13:07 discourse.patroni-pg-backup.log
-rw-r----- 1 postgres postgres  569 Oct 11 13:07 discourse.patroni-pg-expire.log
-rw-r----- 1 postgres postgres 1.5K Oct 11 13:05 discourse.patroni-pg-stanza-create.log
```

The naming convention of the Pgbackrest logs is `<model name>.patroni-<postgresql app name>-<action>.log`. Log output should look similar to:

```text
> cat /var/log/pgbackrest/discourse.patroni-pg-expire.log 
-------------------PROCESS START-------------------
2023-10-11 13:07:44.793 P00   INFO: expire command begin 2.47: --exec-id=843-b0d896e1 --log-level-console=debug --repo1-path=/postgresql-test --repo1-retention-full=9999999 --repo1-s3-bucket=dragop-test-bucket --repo1-s3-endpoint=https://s3.eu-central-1.amazonaws.com --repo1-s3-key=<redacted> --repo1-s3-key-secret=<redacted> --repo1-s3-region=eu-central-1 --repo1-s3-uri-style=host --repo1-type=s3 --stanza=discourse.patroni-pg
2023-10-11 13:07:45.146 P00   INFO: expire command end: completed successfully (353ms)
root@pg-0:/# cat /var/log/pgbackrest/discourse.patroni-pg-backup.log 
-------------------PROCESS START-------------------
2023-10-11 13:06:29.857 P00   INFO: backup command begin 2.47: --no-backup-standby --exec-id=843-b0d896e1 --log-level-console=debug --pg1-path=/var/lib/pg/data/16/main --pg1-user=backup --repo1-path=/postgresql-test --repo1-retention-full=9999999 --repo1-s3-bucket=dragop-test-bucket --repo1-s3-endpoint=https://s3.eu-central-1.amazonaws.com --repo1-s3-key=<redacted> --repo1-s3-key-secret=<redacted> --repo1-s3-region=eu-central-1 --repo1-s3-uri-style=host --repo1-type=s3 --stanza=discourse.patroni-pg --start-fast --type=full
2023-10-11 13:06:30.869 P00   INFO: execute non-exclusive backup start: backup begins after the requested immediate checkpoint completes
2023-10-11 13:06:31.671 P00   INFO: backup start archive = 000000010000000000000004, lsn = 0/4000060
2023-10-11 13:06:31.671 P00   INFO: check archive for prior segment 000000010000000000000003
2023-10-11 13:07:41.913 P00   INFO: execute non-exclusive backup stop and wait for all WAL segments to archive
2023-10-11 13:07:42.413 P00   INFO: backup stop archive = 000000010000000000000004, lsn = 0/4000170
2023-10-11 13:07:42.713 P00   INFO: check archive for segment(s) 000000010000000000000004:000000010000000000000004
2023-10-11 13:07:43.344 P00   INFO: new backup label = 20231011-130630F
2023-10-11 13:07:44.793 P00   INFO: full backup size = 25.2MB, file total = 956
2023-10-11 13:07:44.793 P00   INFO: backup command end: completed successfully (74938ms)
```

## Logs rotation

Charmed PostgreSQL is configured to rotate PostgreSQL text logs every minute and Patroni logs approximately every minute and both are to retain a week's worth of logs.

For PostgreSQL, logs will be truncated when the week turns and the same minute of the same hour of the same weekday comes to pass. E.g. at 12:01 UTC on Monday either a new log file will be created or last week's log will be overwritten.

Due to Patroni only supporting size based rotation, it has been configured to retain logs for a comparatively similar time frame as PostgreSQL. The assumed size of a minute of Patroni logs is 600 bytes, but the estimation is bound to be imprecise. Patroni will retain 10,080 log files (for every minute of a week). The current log is `patroni.log`, when rotating Patroni will append a number to the name of the file and remove logs over the limit.

