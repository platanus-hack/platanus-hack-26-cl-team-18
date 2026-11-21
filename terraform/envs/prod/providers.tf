# Credentials come only from the environment, never from tfvars:
#   RENDER_API_KEY   Account settings -> API keys
#   RENDER_OWNER_ID  workspace id (tea-... or usr-...)
# scripts/tf.sh loads them from the gitignored .env.render at the repo root.
provider "render" {
  # apply returns once Render accepts the change; watch the build in the
  # dashboard. Flip to true if you want apply to block until the deploy is live.
  wait_for_deploy_completion = false
}
