# Module contract, planned against a mocked provider: no API key, no network.
#   terraform -chdir=terraform/modules/render-static-site test
mock_provider "render" {}

variables {
  name     = "team18-web-test"
  repo_url = "https://github.com/example/team18"
  env_vars = { VITE_BACKEND_URL = "https://team18-test.onrender.com" }
}

run "builds_web_and_publishes_dist" {
  command = plan

  assert {
    condition     = render_static_site.this.root_directory == "web" && render_static_site.this.publish_path == "dist"
    error_message = "The site must build in web/ and publish web/dist."
  }

  assert {
    condition     = render_static_site.this.env_vars["VITE_BACKEND_URL"].value == "https://team18-test.onrender.com"
    error_message = "Build-time env vars must reach the site."
  }
}

run "client_routes_fall_back_to_index" {
  command = plan

  assert {
    condition     = one(render_static_site.this.routes).destination == "/index.html"
    error_message = "/screen and /console need the SPA rewrite to index.html."
  }
}

run "secret_looking_build_var_is_rejected" {
  command = plan

  variables {
    env_vars = { VITE_API_KEY = "oops" }
  }

  expect_failures = [var.env_vars]
}
