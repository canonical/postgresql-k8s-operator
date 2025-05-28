# How to enable LDAP authentication

LDAP (*Lightweight Directory Access Protocol*) enables centralized authentication for PostgreSQL clusters, reducing the overhead of managing local credentials and access policies.

This guide goes over the steps to integrate LDAP as an authentication method with the PostgreSQL-K8s charm, all within the Juju ecosystem.

## Prerequisites
* Charmed PostgreSQL channel `14/edge` or `16/edge` (revision `570` or higher)
* Juju `v3.6` or higher

## Deploy an LDAP server

[note type="caution"]
**Disclaimer:** In this guide, we use [self-signed certificates](https://en.wikipedia.org/wiki/Self-signed_certificate) provided by the [`self-signed-certificates` operator](https://github.com/canonical/self-signed-certificates-operator). 

**This is not recommended for a production environment.**

For production environments, check the collection of [Charmhub operators](https://charmhub.io/?q=tls-certificates) that implement the `tls-certificate` interface, and choose the most suitable for your use-case.
[/note]

Deploy the [GLAuth charm](https://charmhub.io/glauth-k8s):

```shell
juju add-model glauth
juju deploy self-signed-certificates
juju deploy postgresql-k8s --channel 14/stable --trust
juju deploy glauth-k8s --channel edge --trust
```

Integrate (formerly known as "relate") the three applications:

```shell
juju integrate glauth-k8s:certificates self-signed-certificates
juju integrate glauth-k8s:pg-database postgresql-k8s
```

Deploy the [GLAuth-utils charm](https://charmhub.io/glauth-utils), in order to manage LDAP users:

```shell
juju deploy glauth-utils --channel edge --trust
```

Integrate (formerly known as "relate") the two applications:

```shell
juju integrate glauth-k8s glauth-utils
```

## Expose cross-model relations

To offer the GLAuth interfaces, run:

```shell
juju offer glauth-k8s:ldap ldap
juju offer glauth-k8s:send-ca-cert send-ca-cert
```

## Enable LDAP

Switch to the Kubernetes controller:

```shell
juju switch <k8s_controller>:postgresql
```

To have LDAP offers consumed:

```shell
juju consume admin/glauth.ldap
juju consume admin/glauth.send-ca-cert
```

To have LDAP authentication enabled, relate the PostgreSQL charm with the GLAuth charm:

```shell
juju integrate postgresql-k8s:ldap ldap
juju integrate postgresql-k8s:receive-ca-cert send-ca-cert 
```

## Map LDAP users to PostgreSQL

To have LDAP users available in PostgreSQL, provide a comma separated list of LDAP groups to already created PostgreSQL authorization groups. To create those groups before hand, refer to the Data Integrator charm [page](https://charmhub.io/data-integrator).

```shell
juju config postgresql-k8s ldap_map="<ldap_group>=<psql_group>"
```

## Disable LDAP

You can disable LDAP by removing the following relations:

```shell
juju remove-relation postgresql-k8s:receive-ca-cert send-ca-cert
juju remove-relation postgresql-k8s:ldap ldap
```