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
- `vibectl init <app>` is one-time provisioning (not idempotent; never run it
  on an existing app) and keeps fly.toml's `app = "coding-vm"` placeholder /
  `primary_region` in sync — don't hardcode a specific app name anywhere.
- Never commit secrets; `fly secrets set` only. Env files (`<app>.env`) are
  gitignored via `*.env` and read via `source` only — the CLI itself never
  opens them.
