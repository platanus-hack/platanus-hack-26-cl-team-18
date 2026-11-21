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
