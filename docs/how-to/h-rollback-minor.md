# Perform a minor rollback
**Example**: PostgreSQL 14.9 -> PostgreSQL 14.8<br/>
(including simple charm revision bump: from revision 43 to revision 42)

[note type="caution"]
**Warning:** Do NOT trigger `rollback` during the running `upgrade` action! It may cause an unpredictable PostgreSQL cluster state!
[/note]

[note]
**Note**: All commands are written for `juju >= v.3.0`

If you are using an earlier version, be aware that:

 - `juju run` replaces `juju run-action --wait` in `juju v.2.9` 
 - `juju integrate` replaces `juju relate` and `juju add-relation` in `juju v.2.9`

For more information, check the [Juju 3.0 Release Notes](https://juju.is/docs/juju/roadmap#heading--juju-3-0-0---22-oct-2022).
[/note]

## Manual rollback

After a `juju refresh`, if there are any version incompatibilities in charm revisions, its dependencies, or any other unexpected failure in the upgrade process, the process will be halted and enter a failure state.

Even if the underlying PostgreSQL cluster continues to work, it’s important to roll back the charm to 
a previous revision so that an update can be attempted after further inspection of the failure.

## Minor rollback steps
1. **Prepare** the Charmed PostgreSQL K8s application for the in-place rollback. 
2. **Rollback**. Perform the first charm rollback - only the first unit. The unit with the maximal ordinal will be chosen.
3. **Resume**. Continue rollback to all other units if the first unit rolled back successfully.
4. **Check**. Make sure the charm and cluster are in healthy state again.

To execute a rollback, we use a similar procedure to the upgrade. The difference is the charm revision to upgrade to. In this guide's example, we will refresh the charm back to revision `88`.

## Step 1: Prepare

It is necessary to re-run `pre-upgrade-check` action on the leader unit to enter the upgrade recovery state:

```shell
juju run postgresql-k8s/leader pre-upgrade-check
```

## Step 2: Rollback

When using a charm from charmhub:

```shell
juju refresh postgresql-k8s --revision=88
```

When deploying from a local charm file, one must have the previous revision charm file and the `postgresql-image` resource, then run

```shell
juju refresh postgresql-k8s --path=./postgresql-k8s_ubuntu-22.04-amd64.charm \
       --resource postgresql-image=ghcr.io/canonical/charmed-postgresql:797a2132
```

...where `postgresql-k8s_ubuntu-22.04-amd64.charm` is the previous revision charm file. The reference for the resource for a given revision can be found at the `metadata.yaml` file in the [charm repository](https://github.com/canonical/postgresql-k8s-operator/blob/main/metadata.yaml#L31).

The first unit will be rolled out and should rejoin the cluster after settling down. After the refresh command, the juju controller revision for the application will be back in sync with the running Charmed PostgreSQL K8s revision.

## Step 3: Resume

We still need to resume the upgrade on the remaining units, which is done with the `resume-upgrade` action.

```shell
juju run postgresql-k8s/leader resume-upgrade
```

This will roll out the pods in the remaining units, but to the same charm revision.

## Step 4: Check

Future [improvements are planned](https://warthogs.atlassian.net/browse/DPE-2620) to check the state on pods/clusters on a low level. At the moment, check `juju status` to make sure the cluster [state](/t/11855) is OK.