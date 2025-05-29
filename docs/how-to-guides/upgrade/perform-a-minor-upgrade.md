


```{note}
**Note**: All commands are written for `juju >= v.3.0`

If you are using an earlier version, check the [Juju 3.0 Release Notes](https://juju.is/docs/juju/roadmap#juju-3-0-0---22-oct-2022).
```

# Perform a minor upgrade

**Example**: PostgreSQL 14.8 -> PostgreSQL 14.9<br/>
(including simple charm revision bump: from revision 99 to revision 102)

This guide is part of [Charmed PostgreSQL K8s Upgrades](/how-to-guides/upgrade/index). Please refer to this page for more information and an overview of the content.

## Summary

- [**Pre-upgrade checks**](#pre-upgrade-checks): Important information to consider before starting an upgrade.
- [**1. Collect**](#step-1-collect) all necessary pre-upgrade information. It will be necessary for a rollback, if needed. **Do not skip this step**; better to be safe than sorry!
- [**2. (Optional) Scale up**](#step-2-scale-up-optional). The new unit will be the first one to be updated, and it will simplify the rollback procedure a lot in case of an upgrade failure.
- [**3. Prepare**](#step-3-prepare) your Charmed PostgreSQL K8s application for the in-place upgrade. See the step details for all technical details executed by charm here.
- [**4. Upgrade** - phase 1](#step-4-upgrade). Once started, only one unit in a cluster will be upgraded. In case of failure, the rollback is simple: remove the pod that was recently added in [Step 2: Scale up](#step-2-scale-up-optional).
- [**5. Resume upgrade** - phase 2](#step-5-resume). If the new pod is OK after the refresh, the upgrade can be resumed for all other units in the cluster. All units in a cluster will be executed sequentially: from biggest ordinal to the lowest one.
- [**4. (Optional) Consider a rollback**](#step-6-rollback-optional) in case of disaster. 
  - Please [inform us](/reference/contacts) about your case scenario troubleshooting to trace the source of the issue and prevent it in the future. 
- [**7. (optional) Scale back**](#step-7-scale-back-optional). Remove no longer necessary K8s pods created in [Step 2: Scale up](#step-2-scale-up-optional) (if any).
- [**Post-upgrade check**](#step-5-post-upgrade-check). Make sure all units are in their proper state and the cluster is healthy.

---

## Pre-upgrade checks
Before performing a minor PostgreSQL upgrade, there are some important considerations to take into account:
* Concurrency with other operations during the upgrade
* Backing up your data
* Service disruption

### Concurrency with other operations
**We strongly recommend to NOT perform any other extraordinary operations on Charmed PostgreSQL K8s cluster while upgrading.** 

Some examples are operations like (but not limited to) the following:

* Adding or removing units
* Creating or destroying new relations
* Changes in workload configuration
* Upgrading other connected/related/integrated applications simultaneously

Concurrency with other operations is not supported, and it can lead the cluster into inconsistent states.
### Backups
**Make sure to have a backup of your data when running any type of upgrade.**

Guides on how to configure backups with S3-compatible storage can be found [here](/how-to-guides/back-up-and-restore/create-a-backup).

### Service disruption
**It is recommended to deploy your application in conjunction with the [Charmed PgBouncer K8s](https://charmhub.io/pgbouncer-k8s) operator.** 

This will ensure minimal service disruption, if any.

## Step 1: Collect
```{note}
This step is only valid when deploying from [charmhub](https://charmhub.io/). 

If a [local charm](https://juju.is/docs/sdk/deploy-a-charm) is deployed (revision is small, e.g. 0-10), make sure the proper/current local revision of the `.charm` file is available BEFORE going further. You might need it for rollback.
```

The first step is to record the revision of the running application as a safety measure for a rollback action. To accomplish this, simply run the `juju status` command and look for the deployed Charmed PostgreSQL revision in the command output, e.g.:

```text
Model        Controller  Cloud/Region        Version  SLA          Timestamp
welcome-k8s  microk8s    microk8s/localhost  3.1.6    unsupported  12:23:03+02:00

App             Version  Status  Scale  Charm           Channel  Rev  Address         Exposed  Message
postgresql-k8s  14.9     active      3  postgresql-k8s  14/beta  145  10.152.183.166  no       

Unit               Workload  Agent  Address     Ports  Message
postgresql-k8s/0*  active    idle   10.1.12.12         Primary
postgresql-k8s/1   active    idle   10.1.12.19         
postgresql-k8s/2   active    idle   10.1.12.20     
```

For this example, the current revision is `145`. Store it safely to use in case of a rollback!

## Step 2: Scale-up (optional)

It is recommended to scale the application up by one unit before starting the upgrade process.

The new unit will be the first one to be updated, and it will assert that the upgrade is possible. In case of failure, having the extra unit will ease the rollback procedure without disrupting service. You can read more about this in the [Minor rollback](/how-to-guides/upgrade/perform-a-minor-rollback) guide.

You can scale your application using the following command:
```text
juju scale-application postgresql-k8s <current_units_count+1>
```
Wait for the new unit to be up and ready.

## Step 3: Prepare

After the application has settled, it’s necessary to run the `pre-upgrade-check` action against the leader unit:

```text
juju run postgresql-k8s/leader pre-upgrade-check
```

Make sure there are no errors in the Juju output.

This action will configure the charm to minimize the amount of primary switchover, among other preparations for the upgrade process. After successful execution, the charm is ready to be upgraded.

## Step 4: Upgrade

Use the [`juju refresh`](https://juju.is/docs/juju/juju-refresh) command to trigger the charm upgrade process. If using juju version 3 or higher, it is necessary to add the `--trust` option.

Example with channel selection and juju 2.9.x:
```text
juju refresh postgresql-k8s --channel 14/edge
```
Example with channel selection and juju 3.x:
```text
juju refresh postgresql-k8s --channel 14/edge --trust
```
Example with specific revision selection (do NOT miss OCI resource!):
```text
juju refresh postgresql-k8s --revision=189 --resource postgresql-image=...
```

### Important Notes
**The upgrade will execute only on the highest ordinal unit.** 

For the running example `postgresql-k8s/3`, the `juju status` will look like:

```text
Model        Controller  Cloud/Region        Version  SLA          Timestamp
welcome-k8s  microk8s    microk8s/localhost  3.1.6    unsupported  12:26:32+02:00

App             Version  Status   Scale  Charm           Channel  Rev  Address         Exposed  Message
postgresql-k8s  14.9     waiting      4  postgresql-k8s  14/edge  154  10.152.183.166  no       installing agent

Unit               Workload     Agent      Address     Ports  Message
postgresql-k8s/0*  waiting      idle       10.1.12.12         other units upgrading first...
postgresql-k8s/1   waiting      idle       10.1.12.19         other units upgrading first...
postgresql-k8s/2   waiting      idle       10.1.12.20         other units upgrading first...
postgresql-k8s/3   maintenance  executing  10.1.12.23         upgrading unit
```
**Do NOT trigger `rollback` procedure during the running `upgrade` procedure.** 
It is expected to have some status changes during the process: `waiting`, `maintenance`, `active`. 

Make sure `upgrade` has failed/stopped and cannot be fixed/continued before triggering `rollback`!

**Please be patient during huge installations.**
The unit should recover shortly after, but the time can vary depending on the amount of data written to the cluster while the unit was not part of it.

## Step 5: Resume

After the unit is upgraded, the charm will set the unit upgrade state as completed. If deemed necessary, the user can further assert the success of the upgrade. 

Given that the unit is healthy within the cluster, the next step is to resume the upgrade process by running:

```text
juju run postgresql-k8s/leader resume-upgrade 
```

The `resume-upgrade` command will roll out the upgrade for the following unit, always from highest to lowest. For each successful upgraded unit, the process will roll out the next one automatically.

Sample `juju status` output:

```text
Model        Controller  Cloud/Region        Version  SLA          Timestamp
welcome-k8s  microk8s    microk8s/localhost  3.1.6    unsupported  12:28:38+02:00

App             Version  Status   Scale  Charm           Channel  Rev  Address         Exposed  Message
postgresql-k8s  14.9     waiting      4  postgresql-k8s  14/edge  154  10.152.183.166  no       installing agent

Unit               Workload     Agent      Address     Ports  Message
postgresql-k8s/0*  waiting      executing  10.1.12.12         other units upgrading first...
postgresql-k8s/1   waiting      executing  10.1.12.19         other units upgrading first...
postgresql-k8s/2   maintenance  executing  10.1.12.24         (config-changed) upgrading unit
postgresql-k8s/3   maintenance  executing  10.1.12.23         upgrade completed
```

## Step 6: Rollback (optional)

This step must be skipped if the upgrade went well! 

Although the underlying PostgreSQL Cluster continues to work, it’s important to roll back the charm to a previous revision so that an update can be attempted after further inspection of the failure. Please switch to the dedicated [minor rollback](/how-to-guides/upgrade/perform-a-minor-rollback) guide for more information about this process.

## Step 7: Scale back (optional)

If the application scale was changed for the upgrade procedure, it is now safe to scale it back to the desired unit count:

```text
juju scale-application postgresql-k8s <unit_count>
```

## Post-upgrade check

Future [improvements are planned](https://warthogs.atlassian.net/browse/DPE-2620) to check the state of a pod/cluster on a low level. 

For now, check `juju status` to make sure the cluster [state](/reference/statuses) is OK.

<!---
**More TODOs:**

* Clearly describe "failure state"!!!
* How to check progress of upgrade (is it failed or running?)?
* Hints how to fix failed upgrade?
* Describe pre-upgrade check: free space, etc.
--->

