output "app_name" {
  value = juju_application.k8s_postgresql.name
}

output "provides" {
  value = {
    database          = "database",
    metrics_endpoint  = "metrics-endpoint",
    grafana_dashboard = "grafana-dashboard"
  }
}

output "requires" {
  value = {
    logging       = "logging"
    certificates  = "certificates"
    s3_parameters = "s3-parameters"
  }
}
