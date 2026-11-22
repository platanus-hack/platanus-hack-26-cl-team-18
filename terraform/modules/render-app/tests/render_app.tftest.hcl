# Module contract, planned against a mocked provider: no API key, no network.
# Plan only: prevent_destroy on the database would block a test teardown.
#   terraform -chdir=terraform/modules/render-app test
mock_provider "render" {}

variables {
  name          = "team18-test"
  environment   = "test"
  region        = "virginia"
  repo_url      = "https://github.com/example/team18"
  start_command = "uv run --frozen python -m app"
}

run "database_url_comes_from_the_managed_postgres" {
  command = plan

  assert {
    # The value (internal_connection_string) is computed, so unknown at plan
    # with Terraform 1.8; assert the wiring exists and cannot be overridden.
    condition     = contains(keys(render_env_group.config.env_vars), "DATABASE_URL")
    error_message = "The service must receive DATABASE_URL from the module's Postgres."
  }

  assert {
    condition     = render_web_service.app.env_vars == null
    error_message = "Service env vars stay empty: a free-plan service can't be updated (env groups can)."
  }

  assert {
    condition     = render_env_group.config.env_vars["APP_ENV"].value == "test"
    error_message = "APP_ENV must equal the environment input."
  }

  assert {
    condition     = render_web_service.app.runtime_source.native_runtime.runtime == "python"
    error_message = "The service must use the Python native runtime."
  }

  assert {
    condition     = length(render_env_group.secrets) == 0
    error_message = "No secret env group without secret_names."
  }
}

run "without_a_database_the_service_runs_in_memory" {
  command = plan

  variables {
    create_database = false
    service_plan    = "free"
  }

  assert {
    condition     = length(render_postgres.db) == 0
    error_message = "create_database = false must not create Postgres."
  }

  assert {
    condition     = !contains(keys(render_env_group.config.env_vars), "DATABASE_URL")
    error_message = "No DATABASE_URL without a database (the backend falls back to memory)."
  }

  assert {
    condition     = render_web_service.app.pre_deploy_command == null
    error_message = "No migration pre-deploy step without a database."
  }
}

run "secret_names_get_placeholder_values_only" {
  command = plan

  variables {
    secret_names = ["ANTHROPIC_API_KEY"]
  }

  assert {
    condition     = render_env_group.secrets[0].env_vars["ANTHROPIC_API_KEY"].value == "set-in-render-dashboard"
    error_message = "Secrets are created with a placeholder; real values never pass through Terraform."
  }

  assert {
    condition     = !contains(keys(render_env_group.config.env_vars), "ANTHROPIC_API_KEY")
    error_message = "Secrets must reach the service through the env group, not plain env_vars."
  }
}

run "secret_looking_plain_env_var_is_rejected" {
  command = plan

  variables {
    env_vars = { ANTHROPIC_API_KEY = "sk-ant-oops" }
  }

  expect_failures = [var.env_vars]
}

run "module_managed_env_vars_cannot_be_overridden" {
  command = plan

  variables {
    env_vars = { DATABASE_URL = "postgres://elsewhere" }
  }

  expect_failures = [var.env_vars]
}

run "unknown_region_is_rejected" {
  command = plan

  variables {
    region = "santiago"
  }

  expect_failures = [var.region]
}
