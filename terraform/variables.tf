variable "app_name" {
  description = "Name of the application in the Juju model."
  type        = string
  default     = "postgresql-k8s"
}

variable "base" {
  description = "Application base"
  type        = string
  default     = "ubuntu@22.04"
}

variable "channel" {
  description = "Charm channel to use when deploying"
  type        = string
  default     = "14/stable"
}

variable "config" {
  description = "Application configuration. Details at https://charmhub.io/postgresql-k8s/configurations"
  type        = map(string)
  default     = {}
}

variable "constraints" {
  description = "Juju constraints to apply for this application."
  type        = string
  default     = ""
}

variable "model_uuid" {
  description = "Juju model UUID"
  type        = string
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
  description = "Storage directives"
  type        = map(string)
  default = {
    pgdata = "10G"
  }
}

variable "units" {
  description = "Number of units to deploy"
  type        = number
  default     = 1
}
