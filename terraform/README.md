# Terraform (Render)

Infrastructure for Render: a web service, Postgres and a secrets env group,
built from `modules/render-app` and instantiated per environment in
`envs/<env>`.

```bash
make tf-check          # offline: fmt + validate + tests (CI runs this)
make tf-plan           # needs .env.render; saves tfplan
make tf-apply          # applies that reviewed plan
make tf-output
```

Guidelines, the first-deploy runbook and the rules are in
[docs/terraform.md](../docs/terraform.md). Read them before changing anything here.
