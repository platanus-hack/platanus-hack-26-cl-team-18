output "web_url" {
  description = "Public player/console URL (put this in platanus-hack-project.jsonc deploy-url)."
  value       = module.web.url
}

output "service_url" {
  description = "Backend URL (HTTP + WS; /health)."
  value       = module.app.service_url
}

output "service_id" {
  value = module.app.service_id
}

output "postgres_id" {
  value = module.app.postgres_id
}

output "database_url_external" {
  description = "Read with: make tf-output OUT=database_url_external"
  value       = module.app.database_url_external
  sensitive   = true
}

output "env_var_names" {
  value = module.app.env_var_names
}
