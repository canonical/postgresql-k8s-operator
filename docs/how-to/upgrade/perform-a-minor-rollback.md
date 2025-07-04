# How to perform a minor rollback

**Example**: PostgreSQL 14.9 -> PostgreSQL 14.8<br/>
(including simple charm revision bump: from revision 43 to revision 42)

After a `juju refresh`, if there are any version incompatibilities in charm revisions, its dependencies, or any other unexpected failure in the upgrade process, the process will be halted and enter a failure state.

Even if the underlying PostgreSQL cluster continues to work, it’s important to roll back the charm to 
a previous revision so that an update can be attempted after further inspection of the failure.

```{caution}
Do NOT trigger `rollback` during the running `upgrade` action! It may cause an unpredictable PostgreSQL cluster state!
```

## Summary of the rollback steps

1. **Prepare** the Charmed PostgreSQL K8s application for the in-place rollback. 
2. **Rollback**. Perform the first charm rollback - only the first unit. The unit with the maximal ordinal will be chosen.
3. **Resume**. Continue rollback to all other units if the first unit rolled back successfully.
4. **Check**. Make sure the charm and cluster are in healthy state again.

## Step 1: Prepare

To execute a rollback, we use a similar procedure to the upgrade. The difference is the charm revision to upgrade to. In this guide's example, we will refresh the charm back to revision `88`.

It is necessary to re-run `pre-upgrade-check` action on the leader unit to enter the upgrade recovery state:

```text
juju run postgresql-k8s/leader pre-upgrade-check
```

## Step 2: Rollback

When using a charm from Charmhub:

```text
juju refresh postgresql-k8s --revision=88
```

When deploying from a local charm file, one must have the previous revision charm file and the `postgresql-image` resource, then run

```text
juju refresh postgresql-k8s --path=./postgresql-k8s_ubuntu-22.04-amd64.charm \
       --resource postgresql-image=ghcr.io/canonical/charmed-postgresql:797a2132
```

...where `postgresql-k8s_ubuntu-22.04-amd64.charm` is the previous revision charm file. The reference for the resource for a given revision can be found at the `metadata.yaml` file in the [charm repository](https://github.com/canonical/postgresql-k8s-operator/blob/main/metadata.yaml#L31).

The first unit will be rolled out and should rejoin the cluster after settling down. After the refresh command, the juju controller revision for the application will be back in sync with the running Charmed PostgreSQL K8s revision.

## Step 3: Resume

We still need to resume the upgrade on the remaining units, which is done with the `resume-upgrade` action.

```text
juju run postgresql-k8s/leader resume-upgrade
```

This will roll out the pods in the remaining units, but to the same charm revision.

## Step 4: Check

Future [improvements are planned](https://warthogs.atlassian.net/browse/DPE-2620) to check the state on pods/clusters on a low level. At the moment, check `juju status` to make sure the cluster [state](/reference/statuses) is OK.

