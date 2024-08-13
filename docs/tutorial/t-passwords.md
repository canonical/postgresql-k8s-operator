> [Charmed PostgreSQL K8s Tutorial](/t/9296) >  5. Manage passwords
# Manage passwords

When we accessed PostgreSQL earlier in this tutorial, we needed to use a password manually. Passwords help to secure our database and are essential for security. Over time, it is a good practice to change the password frequently. 

In this section, we will go through setting and changing the password for the admin user.

## Summary
- [Retrieve the operator password](#heading--retrieve-password)
- [Rotate the operator password](#heading--rotate-password)
- [Set a new password](#heading--set-new-password)
  - ...for the operator user
  - ...for another user

---

<a href="#heading--retrieve-password"><h2 id="heading--retrieve-password"> Retrieve the operator password </h2></a>

The operator's password can be retrieved by running the `get-password` action on the Charmed PostgreSQL K8s application:
```shell
juju run postgresql-k8s/leader get-password
```

Running the command above should output:
```yaml
unit-postgresql-k8s-0:
  UnitId: postgresql-k8s/0
  id: "6"
  results:
    password: SYhCduijXTAfg9mU
  status: completed
  timing:
    completed: 2023-03-20 11:10:33 +0000 UTC
    enqueued: 2023-03-20 11:10:32 +0000 UTC
    started: 2023-03-20 11:10:33 +0000 UTC
```

<a href="#heading--rotate-password"><h2 id="heading--rotate-password"> Rotate the operator password </h2></a>

You can change the operator's password to a new random password by entering:
```shell
juju run postgresql-k8s/leader set-password
```
Running the command above should output:
```yaml
unit-postgresql-k8s-0:
  UnitId: postgresql-k8s/0
  id: "8"
  results:
    password: 7CYrRiBrC4du3ToX
  status: completed
  timing:
    completed: 2023-03-20 11:10:47 +0000 UTC
    enqueued: 2023-03-20 11:10:46 +0000 UTC
    started: 2023-03-20 11:10:47 +0000 UTC
```
The `status: completed` element in the output above indicates that the password has been successfully updated. The new password should be different from the previous password.

<a href="#heading--set-new-password"><h2 id="heading--set-new-password"> Set a new password... </h2></a>

You can set a specific password for any user by running the `set-password` juju action on the leader unit.

### ...for the operator user
To set a manual password for the operator/admin user, run the following command:

```shell
juju run postgresql-k8s/leader set-password password=my-password
```

where `my-password` is your password of choice.

Example output:

```yaml
unit-postgresql-k8s-0:
  UnitId: postgresql-k8s/0
  id: "10"
  results:
    password: my-password
  status: completed
  timing:
    completed: 2023-03-20 11:11:06 +0000 UTC
    enqueued: 2023-03-20 11:11:02 +0000 UTC
    started: 2023-03-20 11:11:05 +0000 UTC
```

### ...for another user

To set a manual password for another user, run the following command:

```shell
juju run postgresql-k8s/leader set-password username=my-user password=my-password
```
Read more about internal operator users [here](/t/charmed-postgresql-k8s-explanations-users/10843).

**Next step:**  [6. Integrate with other applications](/t/9301)