terraform {
  required_version = ">= 1.8.0"

  required_providers {
    render = {
      source  = "render-oss/render"
      version = "~> 1.9"
    }
  }

  # Local state (terraform/envs/prod/terraform.tfstate, gitignored). It holds
  # the database password, so never commit or paste it. Only one person applies
  # at a time; to share state, switch to the HCP Terraform block in
  # docs/terraform.md#sharing-state.
  backend "local" {}
}
