# AGENTS.md — fly-coding-vm

This repo defines a Fly.io Machine that runs an OpenCode server as a remote
coding VM. See `README.md` for the full picture; this file is just the
working rules.

## Layout

- `vm-tools/` — uv project installed in the Docker image (`start-work`,
  `mint-gh-token`, `watch`).
- `vibectl/` — local-only uv CLI wrapping flyctl (NOT in the image).
- `Dockerfile`, `fly.toml`, `entrypoint.sh`, `skills/`, `home/` — VM image
  and boot files.

## Working rules

- Both Python projects use `uv`; run tests with `uv run pytest` inside
  `vm-tools/` or `vibectl/`.
- Changes to the Dockerfile, `entrypoint.sh`, `skills/`, `home/` or `vm-tools/`
  require `fly deploy` (or `vibectl deploy`) to take effect on the VM.
- `vibectl/` is local-only — never add it to the Dockerfile.
- Never commit secrets; `fly secrets set` only. `.env` is gitignored and
  read via `source` only.
