# vmctl

Local-only CLI for the [fly-coding-vm](../README.md). Runs on **your** machine
(where `flyctl` is installed); it is deliberately **not** part of the Docker
image. See the root README's "Local CLI (vmctl)" section for the full command
list and env vars.

Config is read **only** from environment variables (`FLY_APP`, optional
`FLY_MACHINE_ID`, `OC_PORT`, `OPENCODE_PASSWORD`). vmctl never opens a `.env`
file — you `source` it yourself.

```bash
uv tool install ./vmctl                 # or: uvx --from ./vmctl vmctl --help
uv run --project vmctl vmctl --help     # run from source without installing
vmctl --dry-run start                   # print the fly commands (no flyctl needed)
```

## Dev

```bash
uv run pytest                 # from this directory (tests/; dev group supplies pytest)
```
