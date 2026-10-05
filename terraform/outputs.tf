output "app_name" {
  value = juju_application.k8s_postgresql.name
}

output "provides" {
  value = {
    database          = "database"
    db                = "db"
    db_admin          = "db-admin"
    metrics_endpoint  = "metrics-endpoint"
    grafana_dashboard = "grafana-dashboard"
    replication_offer = "replication-offer"
  }
}

output "requires" {
  value = {
    replication     = "replication"
    certificates    = "certificates"
    receive_ca_cert = "receive-ca-cert"
    s3_parameters   = "s3-parameters"
    ldap            = "ldap"
    logging         = "logging"
    tracing         = "tracing"
  }
}
