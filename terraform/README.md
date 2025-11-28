<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
|------|---------|
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.6.6 |
| <a name="requirement_juju"></a> [juju](#requirement\_juju) | ~> 1.0.0 |

## Providers

| Name | Version |
|------|---------|
| <a name="provider_juju"></a> [juju](#provider\_juju) | ~> 1.0.0 |

## Modules

No modules.

## Resources

| Name | Type |
|------|------|
| [juju_application.k8s_postgresql](https://registry.terraform.io/providers/juju/juju/latest/docs/resources/application) | resource |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_app_name"></a> [app\_name](#input\_app\_name) | Name of the application in the Juju model. | `string` | `"postgresql-k8s"` | no |
| <a name="input_base"></a> [base](#input\_base) | Application base | `string` | `"ubuntu@24.04"` | no |
| <a name="input_channel"></a> [channel](#input\_channel) | Charm channel to use when deploying | `string` | `"16/stable"` | no |
| <a name="input_config"></a> [config](#input\_config) | Application configuration. Details at https://charmhub.io/postgresql-k8s/configurations | `map(string)` | `{}` | no |
| <a name="input_constraints"></a> [constraints](#input\_constraints) | Juju constraints to apply for this application. | `string` | `"arch=amd64"` | no |
| <a name="input_juju_model"></a> [juju\_model](#input\_juju\_model) | Juju model uuid | `string` | n/a | yes |
| <a name="input_resources"></a> [resources](#input\_resources) | Resources to use with the application | `map(string)` | `{}` | no |
| <a name="input_revision"></a> [revision](#input\_revision) | Revision number to deploy charm | `number` | `null` | no |
| <a name="input_storage_directives"></a> [storage\_directives](#input\_storage\_directives) | Storage directives to apply for this application | `map(string)` | `{}` | no |
| <a name="input_units"></a> [units](#input\_units) | Number of units to deploy | `number` | `1` | no |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_application_name"></a> [application\_name](#output\_application\_name) | n/a |
| <a name="output_provides"></a> [provides](#output\_provides) | n/a |
| <a name="output_requires"></a> [requires](#output\_requires) | n/a |
<!-- END_TF_DOCS -->