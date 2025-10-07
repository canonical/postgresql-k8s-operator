# Roles

There are several definitions of roles in Charmed PostgreSQL:
* Predefined PostgreSQL roles
* Instance-level DB/relation-specific roles
  *  LDAP-specific roles 
* Extra user roles relation flag

```{seealso}
For details on how users relate to roles, see [](/explanation/users).
```

## PostgreSQL 16 roles

```text
test123=> SELECT * FROM pg_roles;
           rolname           | rolsuper | rolinherit | rolcreaterole | rolcreatedb | rolcanlogin | rolreplication | rolconnlimit | rolpassword | rolvaliduntil | rolbypassrls | rolconfig |  oid
-----------------------------+----------+------------+---------------+-------------+-------------+----------------+--------------+-------------+---------------+--------------+-----------+-------
 pg_database_owner           | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           |  6171
 pg_read_all_data            | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           |  6181
 pg_write_all_data           | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           |  6182
 pg_monitor                  | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           |  3373
 pg_read_all_settings        | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           |  3374
 pg_read_all_stats           | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           |  3375
 pg_stat_scan_tables         | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           |  3377
 pg_read_server_files        | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           |  4569
 pg_write_server_files       | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           |  4570
 pg_execute_server_program   | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           |  4571
 pg_signal_backend           | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           |  4200
 pg_checkpoint               | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           |  4544
 pg_use_reserved_connections | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           |  4550
 pg_create_subscription      | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           |  6304
...
```

## Charmed PostgreSQL 16 roles

Charmed PostgreSQL 16 introduces the following instance-level predefined roles:

* `charmed_stats` (inherit from pg_monitor)
* `charmed_read` (inherit from pg_read_all_data and `charmed_stats`)
* `charmed_dml` (inherit from pg_write_all_data and `charmed_read`)
* `charmed_backup` (inherit from pg_checkpoint and `charmed_stats`)
* `charmed_dba` (allowed to escalate to any other user, including the superuser `operator`)
* `charmed_admin` (inherit from `charmed_dml` and allowed to escalate to the database-specific `charmed_<database-name>_owner` role, which is explained later in this document)
* `charmed_databases_owner` (allowed to create databases; it can be requested through the CREATEDB extra user role)

Currently, `charmed_backup` and `charmed_dba` cannot be requested through the relation as extra user roles.

```text
test123=> SELECT * FROM pg_roles;
           rolname           | rolsuper | rolinherit | rolcreaterole | rolcreatedb | rolcanlogin | rolreplication | rolconnlimit | rolpassword | rolvaliduntil | rolbypassrls | rolconfig |  oid
-----------------------------+----------+------------+---------------+-------------+-------------+----------------+--------------+-------------+---------------+--------------+-----------+-------
...
 charmed_stats               | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           | 16386
 charmed_read                | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           | 16388
 charmed_dml                 | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           | 16390
 charmed_backup              | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           | 16392
 charmed_dba                 | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           | 16393
 charmed_admin               | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           | 16394
 charmed_databases_owner     | f        | t          | f             | t           | t           | f              |           -1 | ********    |               | f            |           | 16395
...
```

Charmed PostgreSQL 16 also introduces catalogue/database level roles, with permissions tied to each database that's created. Example for a database named `test`:

```text
test123=> SELECT * FROM pg_roles WHERE rolname LIKE 'charmed_test_%';
      rolname       | rolsuper | rolinherit | rolcreaterole | rolcreatedb | rolcanlogin | rolreplication | rolconnlimit | rolpassword | rolvaliduntil | rolbypassrls | rolconfig |  oid
--------------------+----------+------------+---------------+-------------+-------------+----------------+--------------+-------------+---------------+--------------+-----------+-------
 charmed_test_owner | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           | 16396
 charmed_test_admin | f        | f          | f             | f           | f           | f              |           -1 | ********    |               | f            |           | 16397
 charmed_test_dml   | f        | t          | f             | f           | f           | f              |           -1 | ********    |               | f            |           | 16398
```

The `charmed_<database-name>_admin` role is assigned to each relation user (explained in the next section) with access to the specific database. When that user connects to the database, it's auto-escalated to the `charmed_<database-name>_owner` user, which will own every object inside the database, simplifying the permissions to perform operations on those objects when a new user requests access to that same database.

There is also a `charmed_<database-name>_dml` role that is assigned to each relation user to still allow them to read and write to the database objects even if the mechanism to auto-escalate the relation user to the `charmed_<database-name>_owner` role doesn't work.

### Relation-specific roles

For each application/relation, the dedicated user has been created:

```text
postgres=# SELECT * FROM pg_roles;
          rolname           | rolsuper | rolinherit | rolcreaterole | rolcreatedb | rolcanlogin | rolreplication | rolconnlimit | rolpassword | rolvaliduntil | rolbypassrls | rolconfig |  oid
----------------------------+----------+------------+---------------+-------------+-------------+----------------+--------------+-------------+---------------+--------------+-----------+-------
...
 relation_id_12             | f        | t          | t             | t           | t           | f              |           -1 | ********    |               | f            |           | 16416
...

postgres=# SELECT * FROM pg_user;
          usename           | usesysid | usecreatedb | usesuper | userepl | usebypassrls |  passwd  | valuntil | useconfig
----------------------------+----------+-------------+----------+---------+--------------+----------+----------+-----------
 ...
 relation_id_12             |    16416 | t           | f        | f       | f            | ******** |          |
```

When the same application is being related through PgBouncer, the extra users/roles are created following the same logic as above:

```text
postgres=# SELECT * FROM pg_roles;
          rolname           | rolsuper | rolinherit | rolcreaterole | rolcreatedb | rolcanlogin | rolreplication | rolconnlimit | rolpassword | rolvaliduntil | rolbypassrls | rolconfig |  oid
----------------------------+----------+------------+---------------+-------------+-------------+----------------+--------------+-------------+---------------+--------------+-----------+-------
...
 relation-14                | t        | t          | f             | f           | t           | f              |           -1 | ********    |               | f            |           | 16403
 pgbouncer_auth_relation_14 | t        | t          | f             | f           | t           | f              |           -1 | ********    |               | f            |           | 16410
 relation_id_13             | f        | t          | t             | t           | t           | f              |           -1 | ********    |               | f            |           | 16417
...

postgres=# SELECT * FROM pg_user;
          usename           | usesysid | usecreatedb | usesuper | userepl | usebypassrls |  passwd  | valuntil | useconfig
----------------------------+----------+-------------+----------+---------+--------------+----------+----------+-----------
 ...
 relation-14                |    16403 | f           | t        | f       | f            | ******** |          |
 pgbouncer_auth_relation_14 |    16410 | f           | t        | f       | f            | ******** |          |
 relation_id_13             |    16417 | t           | f        | f       | f            | ******** |          |
```

In this case, there are  several records created to:
 * `relation_id_13` - for relation between Application and PgBouncer
 * `relation-14` - for relation between PgBouncer and PostgreSQL
 * `pgbouncer_auth_relation_14` - to authenticate end-users, which connects PgBouncer

## Escalation and switching identity

Regular relation users can temporarily escalate their privileges to other predefined roles using PostgreSQL's role-switching features. There are two approaches:

- SET ROLE / RESET ROLE (standard, built-in)
- The `set_user`

Both approaches are enabled by default in the charm.

Below they are explained in more details, including some examples.

### 1) Using SET ROLE and RESET ROLE

This is the standard PostgreSQL mechanism. A session can switch its current role to any role of which the current role is a member. Use the `SET ROLE` command to switch to a predefined role. Use RESET ROLE to return to the original role.

Example: escalate to `charmed_dml`:

```sql
-- In the application/session for the relation user, escalate to the predefined role.
SET ROLE charmed_dml;

-- Perform privileged operations that charmed_dml allows.

-- Then switch back to the original role.
RESET ROLE;
```

Notes:
- `SET ROLE` only works if the current role is a member of the target role (i.e. the target role was requested through the `extra-user-roles` relation field).

### 2) Using the set_user and reset_user to switch identity to another user

In some deployments you may want a member of `charmed_dba` to be able to become a different PostgreSQL user (either a non-superuser or the cluster superuser) for the duration of a session. The `set_user` extension provides functions that allow a caller to change the session's effective user identity.

Example: switch identity to a non-superuser role:

```sql
-- Called in a session where the relation user is a member of charmed_dba.
SELECT set_user('another_user'::TEXT);

-- Perform actions as another_user.

-- Then switch back to the previous identity. 
SELECT reset_user(); 
```

Example: switch identity to the (cluster) superuser `operator`:

```sql
-- Called in a session where the relation user is a member of charmed_dba.
SELECT set_user_u('operator'::TEXT);

-- Perform superuser operations.

-- Then switch back to the previous identity. 
SELECT reset_user();
```

Important security considerations:
- Prefer using `SET ROLE` when possible; use the `set_user` identity switching approach only when you need to assume an identity that cannot be achieved via role membership.

### Charmed PostgreSQL LDAP roles

To map LDAP users to PostgreSQL users, the dedicated LDAP groups have to be created before hand using [Data Integrator](https://charmhub.io/data-integrator) charm.
The result of such mapping will be a new PostgreSQL Roles:

```text
postgres=# SELECT * FROM pg_roles;
    rolname    | rolsuper | rolinherit | rolcreaterole | rolcreatedb | rolcanlogin | rolreplication | rolconnlimit | rolpassword | rolvaliduntil | rolbypassrls | rolconfig |  oid
----------------------------+----------+------------+---------------+-------------+-------------+----------------+--------------+-------------+---------------+--------------+-----------+-------
...
 myrole        | t        | t          | f             | f           | t           | f              |           -1 | ********    |               | f            |           | 16422
```
