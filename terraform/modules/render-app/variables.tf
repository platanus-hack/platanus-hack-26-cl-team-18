variable "name" {
  description = "Base name for every resource (service, database, env group)."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,40}$", var.name))
    error_message = "name must be lowercase letters, digits and dashes (2-41 chars), starting with a letter."
  }
}

variable "environment" {
  description = "Environment label injected as APP_ENV (e.g. prod, dev)."
  type        = string
}

variable "environment_id" {
  description = "Render project environment id that groups these resources. Null = no project."
  type        = string
  default     = null
}

variable "region" {
  description = "Render region. One of frankfurt, ohio, oregon, singapore, virginia."
  type        = string

  validation {
    condition     = contains(["frankfurt", "ohio", "oregon", "singapore", "virginia"], var.region)
    error_message = "region must be one of frankfurt, ohio, oregon, singapore, virginia."
  }
}

# ---------------------------------------------------------------- web service

variable "repo_url" {
  description = "HTTPS URL of the GitHub repo Render builds from. Either Render has GitHub access to it, or it is public (then auto_deploy must be false: no push webhooks)."
  type        = string

  validation {
    condition     = can(regex("^https://github\\.com/[^/]+/[^/]+$", var.repo_url))
    error_message = "repo_url must look like https://github.com/<owner>/<repo> (no trailing slash or .git)."
  }
}

variable "branch" {
  description = "Git branch Render deploys."
  type        = string
  default     = "main"
}

variable "auto_deploy" {
  description = "Deploy automatically on every push to branch."
  type        = bool
  default     = true
}

variable "service_plan" {
  description = "Render web service plan (starter, standard, pro, ...)."
  type        = string
  default     = "starter"
}

variable "python_version" {
  description = "Exact Python version Render installs (PYTHON_VERSION). Must satisfy pyproject requires-python."
  type        = string
  default     = "3.14.0"
}

variable "build_command" {
  description = "Build command. Installs locked runtime deps only (dev extras excluded)."
  type        = string
  default     = "pip install uv && uv sync --frozen"
}

variable "start_command" {
  description = "Start command. Must bind 0.0.0.0:$PORT."
  type        = string
}

variable "pre_deploy_command" {
  description = "Runs after build, before the new version takes traffic. A failure aborts the deploy (used for migrations)."
  type        = string
  default     = "uv run --frozen python scripts/migrate.py"
}

variable "health_check_path" {
  description = "HTTP path Render polls before routing traffic. Null disables the check."
  type        = string
  default     = null
}

variable "build_filter_paths" {
  description = "Only changes under these paths trigger an auto-deploy. Empty = every change."
  type        = list(string)
  default     = ["app/**", "db/**", "scripts/**", "pyproject.toml", "uv.lock"]
}

variable "env_vars" {
  description = "Plain, NON-secret environment variables. Secrets go in secret_names."
  type        = map(string)
  default     = {}

  validation {
    condition = alltrue([
      for k in keys(var.env_vars) :
      !can(regex("(?i)(SECRET|TOKEN|PASSWORD|API_KEY|PRIVATE_KEY|CREDENTIAL)", k))
    ])
    error_message = "env_vars looks like it holds a secret. Declare the name in secret_names and set its value in the Render dashboard."
  }

  validation {
    condition = alltrue([
      for k in keys(var.env_vars) : !contains(["DATABASE_URL", "PYTHON_VERSION", "APP_ENV"], k)
    ])
    error_message = "DATABASE_URL, PYTHON_VERSION and APP_ENV are managed by the module; do not set them in env_vars."
  }
}

variable "secret_names" {
  description = "Names of secret env vars. Terraform creates them with a placeholder and never touches their values again; set real values in the Render dashboard."
  type        = list(string)
  default     = []
}

# ---------------------------------------------------------------- postgres

variable "create_database" {
  description = "Create a Render Postgres and pass DATABASE_URL. False = no database; the backend runs in memory (free-plan deploys)."
  type        = bool
  default     = true
}

variable "db_plan" {
  description = "Render Postgres plan (free, basic_256mb, basic_1gb, pro_4gb, ...). Free databases expire, see docs/terraform.md."
  type        = string
  default     = "basic_256mb"
}

variable "db_version" {
  description = "Postgres major version."
  type        = string
  default     = "17"
}

variable "db_disk_size_gb" {
  description = "Postgres disk size in GB. Null = plan default."
  type        = number
  default     = null
}

variable "db_name" {
  description = "Database name."
  type        = string
  default     = "app"
}

variable "db_user" {
  description = "Database owner role."
  type        = string
  default     = "app"
}
