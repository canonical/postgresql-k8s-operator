# Perform a major rollback

**Example**: PostgreSQL 15 -> PostgreSQL 14

[note type="negative"]
Currently, this charm only supports PostgreSQL 14. Therefore, only [minor rollbacks](https://discourse.charmhub.io/t/charmed-postgresql-k8s-how-to-minor-rollback/12096) are possible.

Canonical is **NOT** planning to support in-place rollbacks for the major PostgreSQL version change as the old PostgreSQL cluster installation will stay nearby and can be reused for the rollback.
[/note]