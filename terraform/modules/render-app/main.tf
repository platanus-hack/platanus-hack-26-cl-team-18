# One deployable environment on Render: a Python web service, an optional
# Postgres database, an env group with its plain config and one with secrets
# Terraform never sees. Without the database the backend keeps its state in
# memory (DATABASE_URL unset).
#
# Env vars live in env groups, not on the service: render-oss/render 1.9.1 sends
# maintenance_mode on every service update, which Render rejects for free-plan
# services ("maintenance mode can only be configured for non-free tier
# services"). Env groups update fine, so config changes never touch the service.

locals {
  secret_placeholder = "set-in-render-dashboard"

  # Module-managed vars are merged last so env_vars can never override them.
  service_env_vars = merge(
    { for k, v in var.env_vars : k => { value = v } },
    {
      APP_ENV        = { value = var.environment }
      PYTHON_VERSION = { value = var.python_version }
    },
    # Internal URL: private network only, no egress, no public exposure.
    var.create_database ? {
      DATABASE_URL = { value = render_postgres.db[0].connection_info.internal_connection_string }
    } : {},
  )
}

resource "render_postgres" "db" {
  count = var.create_database ? 1 : 0

  name           = "${var.name}-db"
  plan           = var.db_plan
  region         = var.region
  version        = var.db_version
  database_name  = var.db_name
  database_user  = var.db_user
  disk_size_gb   = var.db_disk_size_gb
  environment_id = var.environment_id

  # The database is the only resource whose loss is not fixed by re-applying.
  # A plan that would destroy or replace it fails instead. To tear down on
  # purpose, remove this block in a reviewed commit (docs/terraform.md).
  lifecycle {
    prevent_destroy = true
  }
}

resource "render_web_service" "app" {
  name           = var.name
  plan           = var.service_plan
  region         = var.region
  environment_id = var.environment_id
  start_command  = var.start_command
  # Migrations need the database; Render's free plan also runs no pre-deploy step.
  pre_deploy_command = var.create_database ? var.pre_deploy_command : null
  health_check_path  = var.health_check_path

  runtime_source = {
    native_runtime = {
      runtime       = "python"
      repo_url      = var.repo_url
      branch        = var.branch
      build_command = var.build_command
      auto_deploy   = var.auto_deploy
      # Filters only gate auto-deploys; Render drops them when auto_deploy is off.
      build_filter = var.auto_deploy && length(var.build_filter_paths) > 0 ? {
        paths = var.build_filter_paths
      } : null
    }
  }

  # No env_vars on purpose: config lives in render_env_group.config (see the header).
}

resource "render_env_group" "config" {
  name           = "${var.name}-config"
  environment_id = var.environment_id
  env_vars       = local.service_env_vars
}

resource "render_env_group_link" "config" {
  env_group_id = render_env_group.config.id
  service_ids  = [render_web_service.app.id]
}

# Secret *containers*, not secret values (same rule as a Secret Manager
# setup): Terraform creates each name once with a placeholder, then ignores
# the values so the real ones, set in the dashboard, never enter state or git.
resource "render_env_group" "secrets" {
  count = length(var.secret_names) > 0 ? 1 : 0

  name           = "${var.name}-secrets"
  environment_id = var.environment_id
  env_vars       = { for k in var.secret_names : k => { value = local.secret_placeholder } }

  lifecycle {
    ignore_changes = [env_vars]
  }
}

resource "render_env_group_link" "secrets" {
  count = length(var.secret_names) > 0 ? 1 : 0

  env_group_id = render_env_group.secrets[0].id
  service_ids  = [render_web_service.app.id]
}
