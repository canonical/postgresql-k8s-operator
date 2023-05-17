# Manage Passwords

This is part of the [Charmed PostgreSQL Tutorial](/t/charmed-postgresql-k8s-tutorial-overview/9296?channel=14/stable). Please refer to this page for more information and the overview of the content.

## Passwords
When we accessed PostgreSQL earlier in this tutorial, we needed to use a password manually. Passwords help to secure our database and are essential for security. Over time it is a good practice to change the password frequently. Here we will go through setting and changing the password for the admin user.

### Retrieve the password
As previously mentioned, the operator's password can be retrieved by running the `get-password` action on the Charmed PostgreSQL K8s application:
```shell
juju run-action postgresql-k8s/leader get-password --wait
```
Running the command should output:
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

### Rotate the password
You can change the operator's password to a new random password by entering:
```shell
juju run-action postgresql-k8s/leader set-password --wait
```
Running the command should output:
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
Please notice the `status: completed` above which means the password has been successfully updated. The password should be different from the previous password.

### Set the new password
You can change the password to a specific password by entering:
```shell
juju run-action postgresql-k8s/leader set-password password=my-password --wait
```
Running the command should output:
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
The password should match whatever you passed in when you entered the command.