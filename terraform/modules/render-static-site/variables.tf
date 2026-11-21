variable "name" {
  description = "Static site name (also its default onrender.com subdomain)."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,40}$", var.name))
    error_message = "name must be lowercase letters, digits and dashes (2-41 chars), starting with a letter."
  }
}

variable "environment_id" {
  description = "Render project environment id that groups this site. Null = no project."
  type        = string
  default     = null
}

variable "repo_url" {
  description = "HTTPS URL of the GitHub repo Render builds from (a repo Render can access)."
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

variable "root_directory" {
  description = "Repo subdirectory the build runs in."
  type        = string
  default     = "web"
}

variable "build_command" {
  description = "Build command, run inside root_directory."
  type        = string
  default     = "npm ci && npm run build"
}

variable "publish_path" {
  description = "Directory with the built files, relative to root_directory."
  type        = string
  default     = "dist"
}

variable "build_filter_paths" {
  description = "Only changes under these paths (relative to the repo root) trigger an auto-deploy. Empty = every change."
  type        = list(string)
  default     = ["web/**"]
}

variable "spa_fallback" {
  description = "Rewrite unknown paths to /index.html (client-side routes)."
  type        = bool
  default     = true
}

variable "env_vars" {
  description = "Build-time env vars. They end up in a public JS bundle, so never put a secret here."
  type        = map(string)
  default     = {}

  validation {
    condition = alltrue([
      for k in keys(var.env_vars) :
      !can(regex("(?i)(SECRET|TOKEN|PASSWORD|API_KEY|PRIVATE_KEY|CREDENTIAL)", k))
    ])
    error_message = "Static site env vars are baked into a public bundle; secrets are not allowed."
  }
}
