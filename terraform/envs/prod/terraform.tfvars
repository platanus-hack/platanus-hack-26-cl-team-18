# TRACKED ON PURPOSE. Non-secret prod configuration only: anything here shows
# up in git diff and review. Never put a secret value in this file; list its
# name in secret_names and set the value in the Render dashboard.

# Render can only build repos your GitHub account owns, so this is the personal
# mirror from README.md ("Deploying & integrations"), not the org repo.
repo_url = "https://github.com/CHANGE-ME/platanus-hack-26-cl-team-18"

# app/main.py (FastAPI). Must bind $PORT.
start_command = "uv run --frozen uvicorn app.main:app --host 0.0.0.0 --port $PORT"

health_check_path = "/health"

env_vars = {
  LOG_LEVEL = "INFO"
}

secret_names = [
  # "ANTHROPIC_API_KEY",
]
