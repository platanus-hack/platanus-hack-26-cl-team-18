# TRACKED ON PURPOSE. Non-secret prod configuration only: anything here shows
# up in git diff and review. Never put a secret value in this file; list its
# name in secret_names and set the value in the Render dashboard.

# Render's GitHub app must have access to this repository.
repo_url = "https://github.com/platanus-hack/platanus-hack-26-cl-team-18"

# app/main.py (FastAPI). Must bind $PORT.
start_command = "uv run --frozen uvicorn app.main:app --host 0.0.0.0 --port $PORT"

health_check_path = "/health"

env_vars = {
  LOG_LEVEL = "INFO"
  # Pick one with `uv run python -m scripts.elevenlabs_voices`; without it the
  # creatives play silent.
  # ELEVENLABS_NARRATOR_VOICE_ID = ""
}

# Values are set in the Render dashboard (<name>-secrets env group). Names
# added after the first apply are ignored: add those in the dashboard too.
secret_names = [
  "ANTHROPIC_API_KEY",
  "ELEVENLABS_API_KEY",
  # Reserved for /ws/agent auth (not enforced yet): the public URL must not
  # accept audience snapshots from anyone. Declared now because names added
  # after the first apply are ignored.
  "AGENT_TOKEN",
  "DEMO_KEY", # 24+ chars (openssl rand -hex 16): bids, stock and campaigns in prod
]
