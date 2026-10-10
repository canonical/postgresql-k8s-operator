mock_provider "juju" {}

variables {
  juju_model = "11111111-2222-4333-8444-555555555555"
}

run "defaults_reach_the_application" {
  command = plan

  assert {
    condition     = juju_application.k8s_postgresql.name == "postgresql-k8s"
    error_message = "app_name must default to \"postgresql-k8s\""
  }

  assert {
    condition     = juju_application.k8s_postgresql.model_uuid == "11111111-2222-4333-8444-555555555555"
    error_message = "juju_model must be forwarded to the application as its model_uuid"
  }

  assert {
    condition     = juju_application.k8s_postgresql.charm[0].name == "postgresql-k8s"
    error_message = "the module must deploy the \"postgresql-k8s\" charm"
  }

  assert {
    condition     = juju_application.k8s_postgresql.charm[0].channel == "16/stable"
    error_message = "channel must default to \"16/stable\""
  }

  assert {
    condition     = juju_application.k8s_postgresql.charm[0].base == "ubuntu@24.04"
    error_message = "base must default to \"ubuntu@24.04\""
  }

  assert {
    condition     = var.revision == null
    error_message = "revision must default to null so the channel decides the revision"
  }

  assert {
    condition     = juju_application.k8s_postgresql.trust == true
    error_message = "the application must be trusted, the charm needs it to manage its K8s resources"
  }

  assert {
    condition     = juju_application.k8s_postgresql.units == 1
    error_message = "units must default to 1"
  }

  assert {
    condition     = juju_application.k8s_postgresql.constraints == "arch=amd64"
    error_message = "constraints must default to \"arch=amd64\""
  }

  assert {
    condition     = length(var.storage_directives) == 0
    error_message = "storage_directives must default to empty so the charm's storage defaults apply"
  }

  assert {
    condition     = length(var.resources) == 0
    error_message = "resources must default to empty so the charm revision decides the OCI image"
  }
}

run "inputs_are_passed_through" {
  command = plan

  variables {
    app_name           = "pg-custom"
    channel            = "16/edge"
    base               = "ubuntu@26.04"
    units              = 3
    constraints        = "arch=arm64 mem=4G"
    config             = { profile = "testing", plugin_hstore_enable = "true" }
    storage_directives = { data = "20G", archive = "5G" }
    resources          = { postgresql-image = "42" }
  }

  assert {
    condition     = juju_application.k8s_postgresql.name == "pg-custom"
    error_message = "app_name must be used as the application name"
  }

  assert {
    condition     = juju_application.k8s_postgresql.charm[0].channel == "16/edge"
    error_message = "channel must be passed through to the charm block"
  }

  assert {
    condition     = juju_application.k8s_postgresql.charm[0].base == "ubuntu@26.04"
    error_message = "base must be passed through to the charm block"
  }

  assert {
    condition     = juju_application.k8s_postgresql.units == 3
    error_message = "units must be forwarded to the application"
  }

  assert {
    condition     = juju_application.k8s_postgresql.constraints == "arch=arm64 mem=4G"
    error_message = "constraints must be passed through unmodified"
  }

  assert {
    condition     = juju_application.k8s_postgresql.config["profile"] == "testing"
    error_message = "config must be passed through unmodified"
  }

  assert {
    condition     = juju_application.k8s_postgresql.storage_directives == tomap({ data = "20G", archive = "5G" })
    error_message = "storage_directives must be passed through unmodified"
  }

  assert {
    condition     = juju_application.k8s_postgresql.resources["postgresql-image"] == "42"
    error_message = "resources must be passed through unmodified"
  }
}

run "pinned_revision_is_passed_through" {
  command = plan

  variables {
    revision = 495
  }

  assert {
    condition     = juju_application.k8s_postgresql.charm[0].revision == 495
    error_message = "revision must be forwarded to the charm block when pinned"
  }
}
