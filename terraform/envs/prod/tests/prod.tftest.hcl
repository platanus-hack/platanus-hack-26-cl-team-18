# Plans the real prod root (tracked terraform.tfvars + variables.tf defaults)
# against a mocked provider, so CI proves prod is valid without credentials.
#   make tf-test
mock_provider "render" {}

run "prod_plans_with_tracked_inputs" {
  command = plan

  assert {
    condition     = module.app.env_var_names == sort(module.app.env_var_names)
    error_message = "env_var_names output must be sorted."
  }

  assert {
    condition     = contains(module.app.env_var_names, "DATABASE_URL") == var.create_database
    error_message = "Prod receives DATABASE_URL exactly when it has a database."
  }
}

run "prod_environment_is_protected" {
  command = plan

  assert {
    condition     = render_project.this.environments[var.environment].protected_status == "protected"
    error_message = "The prod environment must stay protected."
  }
}

run "web_is_built_against_the_backend" {
  command = plan

  assert {
    condition     = contains(keys(render_env_group.web_origin.env_vars), "CORS_ORIGINS")
    error_message = "The backend must allow the deployed web origin (CORS_ORIGINS)."
  }

  assert {
    condition     = contains(module.app.env_var_names, "ANTHROPIC_API_KEY")
    error_message = "The Claude key must be a declared secret."
  }
}
