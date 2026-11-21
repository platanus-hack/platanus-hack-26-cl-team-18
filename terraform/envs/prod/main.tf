resource "render_project" "this" {
  name = var.project_name

  environments = {
    (var.environment) = {
      name = var.environment
      # Protected environments restrict destructive dashboard actions to admins.
      protected_status = "protected"
    }
  }
}

module "app" {
  source = "../../modules/render-app"

  name           = "${var.project_name}-${var.environment}"
  environment    = var.environment
  environment_id = render_project.this.environments[var.environment].id
  region         = var.region

  repo_url           = var.repo_url
  branch             = var.branch
  auto_deploy        = var.auto_deploy
  service_plan       = var.service_plan
  start_command      = var.start_command
  pre_deploy_command = var.pre_deploy_command
  health_check_path  = var.health_check_path
  env_vars           = var.env_vars
  secret_names       = var.secret_names

  db_plan = var.db_plan
}

# Player (/screen) and console (/console): the Vite build in web/, pointed at
# the backend above. VITE_BACKEND_URL is inlined at build time.
module "web" {
  source = "../../modules/render-static-site"

  name           = "${var.project_name}-${var.environment}-web"
  environment_id = render_project.this.environments[var.environment].id

  repo_url    = var.repo_url
  branch      = var.branch
  auto_deploy = var.auto_deploy

  env_vars = {
    VITE_BACKEND_URL = module.app.service_url
    NODE_VERSION     = var.node_version
  }
}

# The backend must allow the site's origin (the player fetch()es voice-over
# MP3s from /media). The site needs the backend URL and the backend needs the
# site URL; a separate env group linked after both exist breaks that cycle.
# Render redeploys a service when a linked env group changes.
resource "render_env_group" "web_origin" {
  name           = "${var.project_name}-${var.environment}-web-origin"
  environment_id = render_project.this.environments[var.environment].id

  env_vars = {
    CORS_ORIGINS = { value = jsonencode(concat([module.web.url], var.extra_cors_origins)) }
  }
}

resource "render_env_group_link" "web_origin" {
  env_group_id = render_env_group.web_origin.id
  service_ids  = [module.app.service_id]
}
