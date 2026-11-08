# AGENTS.md — fly-coding-vm

This repo defines a Fly.io Machine that runs an OpenCode server as a remote
coding VM. See `README.md` for the full picture; this file is just the
working rules.

## Layout

- `vm-tools/` — uv project installed in the Docker image (`start-work`,
  `mint-gh-token`, `watch`).
- `vibectl/` — local-only uv CLI: one-time `init` (provisions app + volume +
  secrets, writes `<app>.env`) plus control commands (NOT in the image).
- `Dockerfile`, `fly.toml`, `entrypoint.sh`, `skills/`, `home/` — VM image
  and boot files.

## Working rules

- Both Python projects use `uv`; run tests with `uv run pytest` inside
  `vm-tools/` or `vibectl/`.
- Changes to the Dockerfile, `entrypoint.sh`, `skills/`, `home/` or `vm-tools/`
  require `fly deploy` (or `vibectl deploy`) to take effect on the VM.
- `vibectl/` is local-only — never add it to the Dockerfile.
- `vibectl init <app>` is one-time provisioning (not idempotent) for a NEW
  app; `<app>.env` is claimed atomically before any fly call (all-commented
  stub) and finalized afterwards — an existing env file is never overwritten
  (the user deletes it or passes `--env-file`).
- Deploys always run from a fresh temp copy of the build context; `fly.toml`
  is never mutated by vibectl. Per-app region lives in the env file as
  `FLY_REGION`; don't hardcode a specific app name or region anywhere.
- Never commit secrets; `fly secrets set` only. Env files (`<app>.env`) are
  gitignored via `*.env` and read via `source` only — the CLI itself never
  opens them.
