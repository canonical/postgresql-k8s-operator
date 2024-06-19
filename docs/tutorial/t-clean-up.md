> [Charmed PostgreSQL K8s Tutorial](/t/9296) >  7. Clean up environment

# Clean up you environment 

In this tutorial we've successfully deployed PostgreSQL on MicroK8s, added/removed cluster members, added/removed users to/from the database, and even enabled and disabled TLS. 

You may now keep your Charmed PostgreSQL K8s deployment running and write to the database or remove it entirely using the steps in this page. 

---

## Stop your virtual machine
If you'd like to keep your environment for later, simply stop your VM with
```shell
multipass stop my- vm
```

## Delete your virtual machine
If you're done with testing and would like to free up resources on your machine, you can remove the VM entirely.

[note type="caution"]
**Warning**: When you remove VM as shown below, you will lose all the data in PostgreSQL and any other applications inside Multipass VM! 

For more information, see the docs for [`multipass delete`](https://multipass.run/docs/delete-command).
[/note]

**Delete your VM and its data** with
```shell
multipass delete --purge my-vm
```

## Next Steps
If you're looking for what to do next, you can:
- Run [Charmed PostgreSQL on VMs](https://github.com/canonical/postgresql-operator).
- Check out our Charmed offerings of [MySQL](https://charmhub.io/mysql-k8s) and [Kafka](https://charmhub.io/kafka-k8s?channel=edge).
- Read about [High Availability Best Practices](https://canonical.com/blog/database-high-availability)
- [Report](https://github.com/canonical/postgresql-k8s-operator/issues) any problems you encountered.
- [Give us your feedback](https://chat.charmhub.io/charmhub/channels/data-platform).
- [Contribute to the code base](https://github.com/canonical/postgresql-k8s-operator)