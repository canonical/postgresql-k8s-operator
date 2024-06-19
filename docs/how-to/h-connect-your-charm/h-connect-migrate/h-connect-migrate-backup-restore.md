# Migrate database data using ‘backup/restore’

This is a guide for migrating data from modern charms. To migrate [legacy charms](/t/11013) data, refer to the guide [Migrate data via pg_dump](/t/12162).

This Charmed PostgreSQL K8s operator is able to restore its own [backups](/t/9597) stored on [S3-compatible storage](/t/9595). The same restore approach is applicable to [restore backups made by a different installation](/t/9598) of Charmed PostgreSQL, or even another PostgreSQL charm. (The backups have to be created manually using [pgBackRest](https://pgbackrest.org/)!)

[note type="caution"]
**Warning:** The Canonical Data Team describes here the general approach and does NOT support nor guarantee the restoration results. 

Always test a migration in a safe environment before performing it in production!
[/note]

## Prerequisites
* **Check [your application compatibility](/t/11013)** with Charmed PostgreSQL K8s before migrating production data from legacy charm
* Make sure **PostgreSQL versions are identical** before the migration

## Migrate database data
Below is the *general approach* to the migration (see warning above!):

1. **Retrieve root/admin-level credentials from the legacy charm.**

   See examples [here](/t/12162).

2. **Install [pgBackRest](https://pgbackrest.org/) inside the old charm OR nearby.** 
   
   Ensure the version is compatible with pgBackRest in the new `Charmed PostgreSQL K8s` revision you are going to deploy! See examples [here](https://pgbackrest.org/user-guide.html#installation). 

   **Note**: You can use `charmed-postgresql` [SNAP](https://snapcraft.io/charmed-postgresql)/[ROCK](https://github.com/canonical/charmed-postgresql-rock) directly. More details [here](/t/11856#hld).

3. **Configure storage for database backup (local or remote, S3-based is recommended).**

4. **Create a first full logical backup during the off-peak** 

   See an example of backup command [here](https://github.com/canonical/postgresql-k8s-operator/commit/f39caaa4c5c85afdb157bd53df54a24a1b9687ac#diff-cc5993b9da2438ecff27897b3ab9d2f9bc445cbf5b4f6369a1a0c2f404fe6a4fR186-R212).

5. **[Migrate this backup](/t/9598) to the Charmed PostgreSQL installation in your test environment.**
6. **Perform all the necessary tests to make sure your application accepted the new database**
7. **Schedule and perform the final production migration, re-using the last steps above.**
---
Do you have questions? [Contact us](/t/11852) if you are interested in such a data migration!