output "application_name" {
  value = juju_application.k8s_postgresql.name
}


output "provides" {
  value = {
    database          = "database"
    metrics_endpoint  = "metrics-endpoint"
    grafana_dashboard = "grafana-dashboard"
    replication_offer = "replication-offer"
  }
}

output "requires" {
  value = {
    replication         = "replication"
    peer_certificates   = "peer-certificates"
    client_certificates = "client-certificates"
    receive_ca_cert     = "receive-ca-cert"
    s3_parameters       = "s3-parameters"
    ldap                = "ldap"
    logging             = "logging"
    tracing             = "tracing"
  }
}
