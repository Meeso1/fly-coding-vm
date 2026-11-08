# fly-vm-tools

One [uv](https://docs.astral.sh/uv/) project holding the small CLI tools used on
a Fly.io coding VM (see the root README). Installed into the VM image as the
`vm-tools` command (see `../Dockerfile`); the VM's OpenCode skills instruct
the agent to run these.

## Commands

```bash
vm-tools mint-gh-token <owner/repo> [--save]
#   Mints a 1-hour GitHub App installation access token scoped to that single
#   repo (enforced by GitHub via the `repositories` mint parameter) and prints
#   it. --save also stores it 0600 in $VM_STATE_DIR/gh_token (default /data/state).
#   Requested permissions are auto-intersected with the installation's granted
#   permissions, so a minimal `Contents: write` + `Metadata: read` install works
#   (no `Pull requests` grant required; avoids HTTP 422).
#   Needs env: GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY (PEM).

vm-tools start-work <owner/repo> [branch]
#   Clones (or fetches) the repo into $VM_WORK_DIR/<repo> (default /root/work),
#   strips the token from the remote URL, sets a credential helper that reads
#   $GITHUB_TOKEN from the environment, stores the fresh token in the state
#   dir, and prints the repo path.

vm-tools watch [--idle-minutes 15] [--max-uptime-hours 12] [--port 4096]
#   Idle watcher (started by entrypoint.sh). Stops the machine via /.fly/api
#   when no session is running and none was updated within the idle window,
#   with a hard max-uptime backstop (12 h).
#   A live $VM_STATE_DIR/connect-pin (written by `vibectl connect`) makes the
#   VM busy so a connected client is never cut off; expired pins (and pins
#   older than this boot's started_at) are ignored.
#   Right before stopping it appends one line to $VM_STATE_DIR/metrics.jsonl
#   with the run's total OpenCode LLM cost (sum of per-session `cost`), the
#   per-session breakdown, start/stop times and the stop reason
#   (idle|max_uptime). `vibectl metrics` displays all past runs.
```

Env overrides (for running outside the VM): `VM_WORK_DIR`, `VM_STATE_DIR`,
`GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`. `IDLE_MINUTES`, `MAX_UPTIME_HOURS`
and `OC_PORT` set the watcher defaults.

## Dev

```bash
uv sync                 # create .venv + lockfile
uv run vm-tools --help
uv run pytest           # unit tests (permission intersection, watcher pin/metrics)
```
