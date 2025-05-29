


```{note}
**Note**: All commands are written for `juju >= v.3.0`

If you are using an earlier version, check the [Juju 3.0 Release Notes](https://juju.is/docs/juju/roadmap#juju-3-0-0---22-oct-2022).
```

# How to enable TLS encryption

```{caution}
**Disclaimer:** In this guide, we use [self-signed certificates](https://en.wikipedia.org/wiki/Self-signed_certificate) provided by the [`self-signed-certificates` operator](https://github.com/canonical/self-signed-certificates-operator). 

**This is not recommended for a production environment.**

For production environments, check the collection of [Charmhub operators](https://charmhub.io/?q=tls-certificates) that implement the `tls-certificate` interface, and choose the most suitable for your use-case.
```


## Enable TLS
Deploy the TLS charm:
```shell
juju deploy self-signed-certificates --config ca-common-name="Tutorial CA"
```

To enable TLS, integrate (formerly known as "relate") the two applications:
```shell
juju integrate postgresql-k8s:certificates self-signed-certificates:certificates
```

## Manage keys
Updates to private keys for certificate signing requests (CSR) can be made via the `set-tls-private-key` action. Note that passing keys to external/internal keys should *only be done with* `base64 -w0`, *not* `cat`. 

With three replicas, this schema should be followed:

Generate a shared internal key:
```shell
openssl genrsa -out internal-key.pem 3072
```
Generate external keys for each unit:
```shell
openssl genrsa -out external-key-0.pem 3072
openssl genrsa -out external-key-1.pem 3072
openssl genrsa -out external-key-2.pem 3072
```
Apply both private keys to each unit. The shared internal key will be applied only to the juju leader.

```shell
juju run postgresql-k8s/0 set-tls-private-key "external-key=$(base64 -w0 external-key-0.pem)"  "internal-key=$(base64 -w0 internal-key.pem)" 
juju run postgresql-k8s/1 set-tls-private-key "external-key=$(base64 -w0 external-key-1.pem)"  "internal-key=$(base64 -w0 internal-key.pem)" 
juju run postgresql-k8s/2 set-tls-private-key "external-key=$(base64 -w0 external-key-2.pem)"  "internal-key=$(base64 -w0 internal-key.pem)" 
```

Updates can also be done with auto-generated keys with

```shell
juju run postgresql-k8s/0 set-tls-private-key
juju run postgresql-k8s/1 set-tls-private-key
juju run postgresql-k8s/2 set-tls-private-key
```

## Disable TLS 
You can disable TLS by removing the integration.
```shell
juju remove-relation postgresql-k8s:certificates self-signed-certificates:certificates
```

