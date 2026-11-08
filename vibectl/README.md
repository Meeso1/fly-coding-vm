# vibectl

Local-only CLI of the [fly-coding-vm](../README.md) repo: **one-time setup**
(`init`) and **day-to-day control** (start/stop/exec/connect/…) of a coding VM
on Fly. Runs on **your** machine (where `flyctl` is installed); it is
deliberately **not** part of the Docker image. See the root README's "Local CLI
(vibectl)" section for the full command list and env vars.

Config is read **only** from environment variables (`FLY_APP`, optional
`FLY_MACHINE_ID`, `OC_PORT`, `OPENCODE_PASSWORD`). vibectl never opens a
`.env` file — `vibectl init <app>` writes one (e.g. `my-vm.env`) for you to
`source` yourself.

```bash
uv tool install ./vibectl                 # or: uvx --from ./vibectl vibectl --help
uv run --project vibectl vibectl --help     # run from source without installing
vibectl --dry-run start                   # print the fly commands (no flyctl needed)
```

## Dev

```bash
uv run pytest                 # from this directory (tests/; dev group supplies pytest)
```
