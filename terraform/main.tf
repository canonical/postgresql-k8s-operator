resource "juju_application" "k8s_postgresql" {
  name       = var.app_name
  model_uuid = var.model_uuid
  trust      = true

  charm {
    name     = "postgresql-k8s"
    channel  = var.channel
    revision = var.revision
    base     = var.base
  }

  storage_directives = var.storage_directives

  machines    = var.machines
  units       = var.machines == null ? var.units : null
  constraints = var.constraints
  config      = var.config
  resources   = var.resources
}
