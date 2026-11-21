output "service_id" {
  description = "Render id of the web service (srv-...)."
  value       = render_web_service.app.id
}

output "service_url" {
  description = "Public onrender.com URL of the web service."
  value       = render_web_service.app.url
}

output "postgres_id" {
  description = "Render id of the Postgres instance (dpg-...)."
  value       = render_postgres.db.id
}

output "database_url_external" {
  description = "External connection string, for psql/migrations from a laptop. Sensitive."
  value       = render_postgres.db.connection_info.external_connection_string
  sensitive   = true
}

output "secrets_env_group_id" {
  description = "Env group holding secret values set in the dashboard (null if no secret_names)."
  value       = length(render_env_group.secrets) > 0 ? render_env_group.secrets[0].id : null
}

output "env_var_names" {
  description = "Env var names the service receives (values omitted)."
  value       = sort(concat(keys(local.service_env_vars), var.secret_names))
}
