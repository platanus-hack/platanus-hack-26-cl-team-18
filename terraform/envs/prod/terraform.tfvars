# TRACKED ON PURPOSE. Non-secret prod configuration only: anything here shows
# up in git diff and review. Never put a secret value in this file; list its
# name in secret_names and set the value in the Render dashboard.

# Public repo: Render builds it from the URL without GitHub app access, but
# then gets no push webhooks, so deploys are manual (dashboard "Manual Deploy"
# or the service's Deploy Hook URL).
repo_url    = "https://github.com/platanus-hack/platanus-hack-26-cl-team-18"
auto_deploy = false

# Free plan: $0, sleeps after 15 min idle (~50 s cold start), no pre-deploy
# step. No Postgres: the backend keeps auctions/charges in memory, so a sleep
# or redeploy resets them. For starter + Postgres, drop these two lines.
service_plan    = "free"
create_database = false

# app/main.py (FastAPI). Must bind $PORT.
# --ws-max-size: uvicorn buffers a whole frame before the app sees it; 4 MiB covers
# the largest keyframe (ws_max_frame_bytes, 2 MB) instead of the 16 MiB default.
start_command = "uv run --frozen uvicorn app.main:app --host 0.0.0.0 --port $PORT --ws-max-size 4194304"

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
  "FAL_KEY", # fal media generation (ImageGenerator / VideoGenerator); unset = no hosted media
  # Device tokens (app/api/device_auth.py), 24+ chars each (openssl rand -hex 16).
  # Unset in prod means closed: no agent connects, no player bills.
  "AGENT_TOKEN",   # the edge agent's bearer token on /ws/agent
  "SCREEN_TOKEN",  # the kiosk player: open /screen?token=... once; without it, view-only
  "DEMO_KEY",      # 24+ chars (openssl rand -hex 16): bids, stock and campaigns in prod
  "CONSOLE_TOKEN", # /ws/console (every bid, charge, budget); unset = open, so set it
]
