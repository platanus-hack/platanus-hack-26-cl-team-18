# A Render static site built from a subdirectory of the repo (the Vite player
# and console in web/). Static sites are free and served from Render's CDN.

resource "render_static_site" "this" {
  name           = var.name
  environment_id = var.environment_id

  repo_url       = var.repo_url
  branch         = var.branch
  auto_deploy    = var.auto_deploy
  root_directory = var.root_directory
  build_command  = var.build_command
  # Relative to root_directory.
  publish_path = var.publish_path

  build_filter = length(var.build_filter_paths) > 0 ? {
    paths = var.build_filter_paths
  } : null

  # Build-time only: Vite inlines VITE_* into the bundle, so a change needs a
  # rebuild (Render redeploys the site when these change).
  env_vars = { for k, v in var.env_vars : k => { value = v } }

  # Client-side routing: /screen and /console are served by index.html.
  # Render serves a matching file first, so /assets/* is unaffected.
  routes = var.spa_fallback ? [
    { type = "rewrite", source = "/*", destination = "/index.html" },
  ] : null

  # Vite fingerprints everything under /assets, so it can be cached forever.
  # /screen runs YOLO on the CPU (ADR 0010); multithreaded WASM needs a
  # cross-origin isolated page. `credentialless` (not require-corp) keeps the
  # backend's logos, video loops and voice-overs loading without CORP headers.
  # Scoped to /screen: the console and demo tools stay as they were. The ORT
  # thread workers load a chunk from /assets, and a worker script in an
  # isolated page needs its own COEP or the browser blocks it
  # (ERR_BLOCKED_BY_RESPONSE). COEP alone doesn't isolate the other pages
  # (that takes COOP too).
  headers = [
    { path = "/assets/*", name = "Cache-Control", value = "public, max-age=31536000, immutable" },
    { path = "/assets/*", name = "Cross-Origin-Embedder-Policy", value = "credentialless" },
    { path = "/screen", name = "Cross-Origin-Opener-Policy", value = "same-origin" },
    { path = "/screen", name = "Cross-Origin-Embedder-Policy", value = "credentialless" },
    { path = "/screen/*", name = "Cross-Origin-Opener-Policy", value = "same-origin" },
    { path = "/screen/*", name = "Cross-Origin-Embedder-Policy", value = "credentialless" },
  ]
}
