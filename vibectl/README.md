# vibectl

Local-only CLI of the [fly-coding-vm](../README.md) repo: **one-time setup**
(`init`) and **day-to-day control** (start/stop/exec/connect/…) of a coding VM
on Fly. Runs on **your** machine (where `flyctl` is installed); it is
deliberately **not** part of the Docker image. See the root README's "Local CLI
(vibectl)" section for the full command list and env vars.

`source` the env file written by `vibectl init` (e.g. `source my-vm.env`) before
other commands; it exports `FLY_APP`, optional `FLY_REGION`, `FLY_MACHINE_ID`,
`OC_PORT` and `OPENCODE_PASSWORD`. Deploys run from a throwaway temp copy of the
build context and can be rerun any time. The local `fly proxy` port is
auto-picked (and recorded in `<app>.port`) unless `OC_PORT` is set.

```bash
uv tool install ./vibectl                 # or: uvx --from ./vibectl vibectl --help
uv run --project vibectl vibectl --help     # run from source without installing
vibectl --dry-run start                   # print the fly commands (no flyctl needed)
```

## What's new

- **Logging:** every fly command, OpenCode API call, exit code and important
  event (proxy start/stop, machine start/stop, deploy, session created, errors)
  is appended, timestamped and secret-redacted, to
  `logs/vibectl-<app>.log` in the folder you invoke vibectl from. Stdout/stderr
  are unchanged and `--dry-run` is logged too (tagged `[DRY]`). Override the
  location with `VIBECTL_LOG_DIR`.
- **No idle-stop while connected:** `vibectl connect` / `proxy` (incl. `--web`)
  pins the VM on `/data` via `fly machine exec` (expiry now+90s, refreshed every
  30s, removed on exit). The watcher treats a live pin as busy. `--dry-run`
  prints the `fly machine exec` equivalents and writes nothing.
- **Cost / metrics:** `vibectl sessions` shows a per-session `cost` column;
  `vibectl metrics` reads `/data/state/metrics.jsonl` via `fly machine exec`
  and prints each past VM run (start/stop, reason, total `llm_cost_usd`,
  per-session costs). `--dry-run` prints the fly equivalents.

## Dev

```bash
uv run pytest                 # from this directory (tests/; dev group supplies pytest)
```
