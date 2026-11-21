# Every value here is non-secret. terraform.tfvars (tracked) restates the
# ones that need a project-specific value; secrets go in secret_names and are
# set in the Render dashboard.

variable "project_name" {
  description = "Render project that groups this environment's resources."
  type        = string
  default     = "team18"
}

variable "environment" {
  description = "Environment name (also APP_ENV)."
  type        = string
  default     = "prod"
}

variable "region" {
  description = "Render region."
  type        = string
  default     = "virginia"
}

variable "repo_url" {
  description = "GitHub repo Render builds from (your personal mirror)."
  type        = string
}

variable "branch" {
  description = "Branch Render deploys."
  type        = string
  default     = "main"
}

variable "auto_deploy" {
  description = "Deploy on every push to branch."
  type        = bool
  default     = true
}

variable "service_plan" {
  description = "Web service plan."
  type        = string
  default     = "starter"
}

variable "start_command" {
  description = "Web service start command. Must bind 0.0.0.0:$PORT."
  type        = string
}

variable "pre_deploy_command" {
  description = "Runs before each deploy takes traffic (migrations)."
  type        = string
  default     = "uv run --frozen python scripts/migrate.py"
}

variable "health_check_path" {
  description = "Health check path, or null."
  type        = string
  default     = null
}

variable "db_plan" {
  description = "Postgres plan."
  type        = string
  default     = "basic_256mb"
}

variable "env_vars" {
  description = "Plain non-secret env vars."
  type        = map(string)
  default     = {}
}

variable "secret_names" {
  description = "Secret env var names; values are set in the Render dashboard."
  type        = list(string)
  default     = []
}

variable "node_version" {
  description = "Node.js version Render uses to build web/ (package.json engines: >=24)."
  type        = string
  default     = "24"
}

variable "extra_cors_origins" {
  description = "Origins allowed to call the backend besides the deployed web site (e.g. a laptop's Vite dev server)."
  type        = list(string)
  default     = []
}
