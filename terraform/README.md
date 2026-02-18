# Terraform module for PostgreSQL K8s Operator

This is a Terraform module facilitating the deployment of the postgresql-k8s charm using
the [Juju Terraform provider](https://github.com/juju/terraform-provider-juju).
For more information, refer to the
[documentation](https://registry.terraform.io/providers/juju/juju/latest/docs)
for the Juju Terraform provider.

## Requirements

This module requires a Juju model to be available. Refer to the [usage](#usage)
section for more details.

## API

### Inputs

This module offers the following configurable units:

| Name                 | Type        | Description                              | Default          | Required |
|----------------------|-------------|------------------------------------------|------------------|:--------:|
| `app_name`           | string      | Application name                         | postgresql-k8s   |          |
| `base`               | string      | Base version to use for deployed charm   | ubuntu@22.04     |          |
| `channel`            | string      | Channel that charm is deployed from      | 14/stable        |          |
| `config`             | map(string) | Application configuration                | {}               |          |
| `constraints`        | string      | Juju constraints to apply                | ""               |          |
| `model_uuid`         | string      | UUID of the model to deploy the charm to |                  |    Y     |
| `revision`           | number      | Revision number of charm to deploy       | null             |          |
| `storage_directives` | map(string) | Storage directives                       | { pgdata = 10G } |          |
| `units`              | number      | Number of units to deploy                | 1                |          |

### Outputs

After applying, the module exports the following outputs:

| Name              | Description                  |
|-------------------|------------------------------|
| `app_name`        | Application name             |
| `provides`        | Map of `provides` endpoints  |
| `requires`        | Map of `requires` endpoints  |

## Usage

Users should ensure that Terraform is aware of the Juju model dependency of the
charm module.

To deploy this module with its required dependency, you can run
the following command:

```shell
terraform apply -var="model_uuid=<MODEL_UUID>" -auto-approve
```

For more configuration options, refer to the [CharmHub documentation](https://charmhub.io/postgresql-k8s/configurations).

