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
    condition     = contains(module.app.env_var_names, "DATABASE_URL")
    error_message = "Prod must receive DATABASE_URL."
  }
}

run "prod_environment_is_protected" {
  command = plan

  assert {
    condition     = render_project.this.environments[var.environment].protected_status == "protected"
    error_message = "The prod environment must stay protected."
  }
}
