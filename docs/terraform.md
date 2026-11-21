# Terraform on Render: guidelines and runbook

Render infrastructure (backend web service, Postgres, the `web/` static site,
env groups, project) is managed
with Terraform through the official `render-oss/render` provider. The layout,
scripts and rules are cut down to what a hackathon needs; each rule below
prevents a concrete failure.

## Layout

```text
terraform/
  modules/render-app/        # backend: web service + Postgres + secrets env group
    tests/*.tftest.hcl       #   module contract, mocked provider
  modules/render-static-site/  # web/ player + console (Vite build, SPA rewrite, free plan)
    tests/*.tftest.hcl
  envs/prod/                 # a root = one state = one environment
    main.tf                  #   render_project + module "app" + module "web" + CORS env group
    variables.tf             #   every input, with tracked defaults
    terraform.tfvars         #   TRACKED: project-specific, non-secret values
    .terraform.lock.hcl      #   TRACKED: exact provider build + checksums
    tests/prod.tftest.hcl    #   plans the real prod inputs, mocked provider
scripts/
  tf.sh                      # wrapper: credentials, flock, plan-file discipline
  terraform_init.sh          # offline-first init from the lock file
  migrate.py                 # SQL migration runner, Render pre-deploy command
.env.render.example          # credentials template -> .env.render (gitignored)
.github/workflows/terraform.yml  # offline gate: fmt + validate + test
```

To add a `dev` environment, copy `envs/prod` to `envs/dev`, change the
`environment` default, then run `make tf-plan ENV=dev`. It gets its own state.

## Commands

| Command | Needs creds | What it does |
|---|---|---|
| `make tf-check` | no | fmt check, validate and `terraform test` on every root (same as CI) |
| `make tf-fmt` | no | format all `.tf` files |
| `make tf-init` | no | init `ENV` from the committed lock file |
| `make tf-plan` | yes | plan `ENV` into `tfplan` and warn about any delete/replace |
| `make tf-apply` | yes | apply exactly that `tfplan`, then delete it |
| `make tf-output [OUT=name]` | yes | outputs (`OUT=database_url_external` for psql) |
| `make migrate` / `migrate-status` | `DATABASE_URL` | run or inspect migrations by hand |
| `make tf-lock` | no | refresh the lock file for every platform after a provider bump |

`ENV` defaults to `prod`. Use `scripts/tf.sh <env> state|import|console ...`
for everything else.

## First deploy

1. **Personal mirror repo.** Render can only build repos your GitHub account
   owns (see README, "Deploying & integrations"). Create the mirror, push to it,
   and install Render's GitHub app on it (Render dashboard → New → connect
   GitHub). Terraform **cannot** grant Render access to a repo.
2. **Credentials.** Run `cp .env.render.example .env.render`, then fill in
   `RENDER_API_KEY` (Account settings → API keys) and `RENDER_OWNER_ID`
   (workspace id, `tea-…`/`usr-…`).
3. **Config.** In `terraform/envs/prod/terraform.tfvars`, set `repo_url` to the
   mirror. `start_command` (uvicorn on `0.0.0.0:$PORT`) and `health_check_path`
   (`/health`) are already set. Add every secret name to `secret_names` **now**
   (see rule 1's caveat).
4. `make tf-check && make tf-plan`. Read the plan. The first plan should only *create*.
5. `make tf-apply`. Render then builds the service from the repo. Watch the
   build log in the dashboard; `apply` does not wait for the deploy.
6. **Secrets.** In the dashboard, open the `<name>-secrets` env group and
   replace each `set-in-render-dashboard` placeholder with the real value.
7. `make tf-output`. Put `web_url` in `platanus-hack-project.jsonc` → `deploy-url`.
   The player is `<web_url>/screen?id=demo-01` and the console is `<web_url>/console`.
   The backend is `service_url` (`/health` shows it's up).

### How the two services find each other

- The static site gets `VITE_BACKEND_URL = service_url` at **build time**
  (Vite inlines it), so the browser connects straight to the backend for HTTP
  and WS.
- The backend allows the site's origin through `CORS_ORIGINS`, which lives in a
  separate `<project>-<env>-web-origin` env group. It can't be a plain
  `env_vars` entry, because the site needs the backend URL and the backend needs
  the site URL. If the first backend deploy finished before the group was
  linked, voice-overs fail on CORS: trigger **Manual Deploy** on the backend
  once. Extra origins (a laptop's Vite) go in `extra_cors_origins`.
- With `APP_ENV=prod` the backend disables `/dev`, `/docs` and the
  `DEMO_SIMULATOR` fixtures. The public URL shows live screens only.

After that, **app deploys are just `git push`** to the mirror's `main`.
`auto_deploy` is on, and `scripts/migrate.py` runs as the pre-deploy command, so
a failed migration aborts the deploy while the old version keeps serving.
Terraform is only for infrastructure changes.

## Rules

1. **Secrets never pass through Terraform.** List the name in `secret_names`
   and set the value in the dashboard. The module creates the name once with a
   placeholder and then ignores its value (`ignore_changes`), so the value never
   reaches git, a plan or state. `env_vars` refuses names that look like
   secrets (`*_KEY`, `*TOKEN*`, `*PASSWORD*`, …).
   - Caveat: after the group exists, a new entry in `secret_names` is **not**
     added, because `ignore_changes` also covers new keys. Add the key in the
     dashboard, and list it in `secret_names` as documentation.
   - `DEMO_KEY` (24+ chars, `openssl rand -hex 16`) unlocks the market controls
     in prod: bids, autobid, stock and campaign drafts at `/demo/<key>`. Unset
     or shorter, prod serves none of them; `/dev` is never served in prod.
   - `AGENT_TOKEN` and `SCREEN_TOKEN` (24+ chars each) guard the live loop in
     prod: without them no agent connects and no player bills
     (`app/api/device_auth.py`). Run the edge agent with `AGENT_TOKEN` in its
     env; open the kiosk once at `/screen?token=<SCREEN_TOKEN>`.
2. **Config lives in tracked files only.** That means `variables.tf` defaults
   plus the tracked `terraform.tfvars`. No `*.auto.tfvars`, no untracked
   overrides, no "I set it on my laptop": a value nobody else can see gets
   silently reverted by the next person's apply.
3. **Change Render through Terraform, not the dashboard.** Dashboard edits to
   managed fields are reverted on the next apply. Secret values and build
   logs are the only exceptions. To bring something created by hand under
   management, run `scripts/tf.sh prod import <address> <id>`.
4. **Plan, read, apply that plan.** `make tf-apply` only applies the saved
   `tfplan`. Terraform rejects it if state moved since the plan. Read every
   `destroy`/`replace` line; `tf-plan` prints them separately.
5. **The database is `prevent_destroy`.** A plan that would delete or replace
   Postgres fails. Changing `name`, `region` or `database_name/user` forces a
   replacement, which means data loss. To tear down on purpose, remove the
   `lifecycle` block in a reviewed commit first.
6. **One operator per environment.** State is local
   (`terraform/envs/prod/terraform.tfstate`, gitignored). Whoever ran the first
   apply owns it. Others ask them, or switch to shared state (below).
   `tf.sh` holds a per-env `flock`. A "state lock" error means another run is
   active; the losing run changed nothing. Run `force-unlock` only once the
   holder is confirmed dead.
7. **State and plan files are secrets.** They contain the Postgres password.
   Never commit, paste or upload `*.tfstate` or `tfplan` (both are gitignored;
   `tf-apply` deletes `tfplan`). Back up the state file somewhere private;
   losing it means re-importing every resource.
8. **The lock file is committed.** It pins the provider build for Linux, macOS
   and Windows. After changing `version` in `versions.tf`, run `make tf-lock`
   and commit the result. `terraform_init.sh` uses installed locked providers
   offline and retries only network failures.
9. **Scripts read outputs, never tfvars.** Use `terraform output`, because
   reading a literal tfvars entry ignores defaults.
10. **A failed apply is not a re-run.** If apply dies halfway, Render may
    already have changed. Run `make tf-plan` again and read what is left; don't
    loop `apply`.
11. **Migrations are forward-only.** Never edit an applied file in
    `db/migrations/`; `migrate.py` fails on a checksum change. Add
    `NNNN_next.sql` instead, numbered after the latest one.
12. **`make tf-check` before pushing `terraform/`.** CI runs the same check.
    New module behaviour gets a `run` block in `tests/*.tftest.hcl` (mocked
    provider, `command = plan`).

## Cost

Defaults: web service `starter` and Postgres `basic_256mb`, both paid plans
billed against the credits. The static site is free. Check current prices at render.com/pricing. The
`free` Postgres plan expires after a few weeks and the free web plan sleeps,
so avoid both for the demo.

When the hackathon is over, stop the spend: delete the `lifecycle` block on the
database, then run `scripts/tf.sh prod plan -destroy` and `make tf-apply`.

## Sharing state

If more than one person needs to apply, move state to HCP Terraform (free
tier). Replace `backend "local" {}` in `envs/prod/versions.tf` with:

```hcl
cloud {
  organization = "<org>"
  workspaces { name = "team18-prod" }
}
```

Then run `terraform login` and `make tf-init`. Terraform offers to migrate
the local state. Set the workspace's execution mode to **Local**, so plans run
on your machine with `.env.render` and HCP only stores and locks the state.
