[note]
**Note**: All commands are written for `juju >= v.3.0`

If you are using an earlier version, check the [Juju 3.0 Release Notes](https://juju.is/docs/juju/roadmap#heading--juju-3-0-0---22-oct-2022).
[/note]


# How to enable LDAP authentication

[note type="caution"]
**Disclaimer:** In this guide, we use [self-signed certificates](https://en.wikipedia.org/wiki/Self-signed_certificate) provided by the [`self-signed-certificates` operator](https://github.com/canonical/self-signed-certificates-operator). 

**This is not recommended for a production environment.**

For production environments, check the collection of [Charmhub operators](https://charmhub.io/?q=tls-certificates) that implement the `tls-certificate` interface, and choose the most suitable for your use-case.
[/note]

## Deploy an LDAP server

Deploy the GLAuth charm:

```shell
juju deploy self-signed-certificates
juju deploy postgresql-k8s --channel 14/stable --trust postgresql-k8s-glauth
juju deploy glauth-k8s --channel edge --trust
```

Integrate (formerly known as "relate") the three applications:

```shell
juju integrate glauth-k8s self-signed-certificates
juju integrate glauth-k8s postgresql-k8s-glauth
```

Deploy the GLAuth-utils charm, in order to manage LDAP users:

```shell
juju deploy glauth-utils --channel edge --trust
```

Integrate (formerly known as "relate") the two applications:

```shell
juju integrate glauth-k8s glauth-utils
```

## Enable LDAP

To have LDAP authentication enabled, relate the PostgreSQL charm with the GLAuth charm:

```shell
juju integrate postgresql-k8s:ldap glauth-k8s:ldap
juju integrate postgresql-k8s:receive-ca-cert glauth-k8s:send-ca-cert 
```

## Map LDAP users to PostgreSQL

To have LDAP users available in PostgreSQL, provide a comma separated list of LDAP groups to already created PostgreSQL authorization groups. To create those groups before hand, refer to the Data Integrator charm [page](https://charmhub.io/data-integrator).

```shell
juju config postgresql-k8s ldap_map="<ldap_group>=<psql_group>"
```

## Disable LDAP

You can disable LDAP by removing the following relations:

```shell
juju remove-relation postgresql-k8s:receive-ca-cert glauth-k8s:send-ca-cert
juju remove-relation postgresql-k8s:ldap glauth-k8s:ldap
```