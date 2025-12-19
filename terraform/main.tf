resource "juju_application" "k8s_postgresql" {
  name  = var.app_name
  trust = true

  charm {
    name     = "postgresql-k8s"
    channel  = var.channel
    revision = var.revision
    base     = var.base
  }

  storage_directives = var.storage_directives

  units       = var.units
  constraints = var.constraints
  config      = var.config
  resources   = var.resources
  model_uuid  = var.juju_model
}
