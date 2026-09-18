variable "app_name" {
  description = "Name of the application in the Juju model."
  type        = string
  default     = "postgresql-k8s"
}

variable "base" {
  description = "Application base"
  type        = string
  default     = "ubuntu@24.04"
}

variable "channel" {
  description = "Charm channel to use when deploying"
  type        = string
  default     = "16/stable"
}

variable "config" {
  description = "Application configuration. Details at https://charmhub.io/postgresql-k8s/configurations"
  type        = map(string)
  default     = {}
}

variable "constraints" {
  description = "Juju constraints to apply for this application."
  type        = string
  default     = "arch=amd64"
}

variable "juju_model" {
  description = "Deprecated: UUID of the Juju model. Use the model UUID input instead"
  type        = string
  default     = null
}

variable "model_uuid" {
  description = "UUID of the Juju model to deploy to"
  type        = string
  default     = null
}

variable "resources" {
  description = "Resources to use with the application"
  type        = map(string)
  default     = {}
}

variable "revision" {
  description = "Revision number to deploy charm"
  type        = number
  default     = null
}

variable "storage_directives" {
  description = "Storage directives to apply for this application"
  type        = map(string)
  default     = {}
}

variable "units" {
  description = "Number of units to deploy"
  type        = number
  default     = 1
}
