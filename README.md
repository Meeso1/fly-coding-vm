# fly-coding-vm

A Fly.io Machine that acts as a remote vibe-coding VM:

- **Image/config are fully deterministic** from this repo: `Dockerfile` +
  `fly.toml`, deployed with `fly deploy` (build via Depot is free; the image
  lands in Fly's registry and the Machine is updated to it).
- **OpenCode server runs on the VM** (`opencode serve`, port 4096). You connect
  from your local machine over Fly's WireGuard private network:
  `fly proxy 4096:4096 -a <app>` + `opencode --server http://127.0.0.1:4096`
  (TUI), the web UI at the same address, or plain `curl -u opencode:$PW`.
- **No public ports.** `fly.toml` defines no services, so nothing is reachable
  from the internet. Access is WireGuard-only (flyctl auth to your org) *plus*
  the OpenCode server password (Basic auth / pairing). Two layers.
- **Self-stopping:** `vm-tools watch` (python, from the `vm-tools/` directory)
  polls the OpenCode API every 30 s — `GET /api/session/active` plus fully
  cursor-paginated `GET /api/session` — and stops the machine via the in-guest
  Machines API unix socket (`/.fly/api`) when no session is running and none
  was updated for `IDLE_MINUTES` (default 15 min), with a hard
  `MAX_UPTIME_HOURS` (default 12 h) backstop. Compute is billed per second, so
  this keeps cost at ~$0.4–1.8 per 8 h of coding (shared-cpu-8x/8 GB in `iad`).
- **Sessions:** prompts go to a *global* session in `$HOME` (`/root`). For repo
  work, the agent uses the **start-repo-work** skill, which runs
  `vm-tools start-work <owner/repo>` (clones into `/root/work/<repo>`) and then
  creates a **new session rooted at the repo** (`location.directory`), so the
  repo's own `AGENTS.md` / `.opencode/skills` apply. The global session stays in
  `$HOME` as the entrypoint. Idle detection looks at *all* sessions, so several
  repo sessions can run in parallel; the VM stops only when everything is
  idle. Rootfs is wiped on stop, so repos are *not* kept on the volume —
  everything is pushed to GitHub. The `/data` volume (5 GB) only holds watcher
  state, logs, the server password, the GitHub token, and the OpenCode data dir
  (symlinked from `~/.local/share/opencode`, so session history survives).
- **GitHub access via a GitHub App** installed on "only select repositories";
  `vm-tools mint-gh-token` mints a 1 h installation token scoped to a *single*
  repo at mint time (`repositories: [...]` param — enforced by GitHub).

## Files

| File | Purpose |
|---|---|
| `fly.toml` | app/region/VM size/restart policy/volume mount; **no services on purpose** |
| `Dockerfile` | tooling: opencode, gh, git, uv + `vm-tools`, tmux, jq, openssl |
| `vm-tools/` | the uv tools project (plain directory, installed as the `vm-tools` command: `start-work`, `mint-gh-token`, `watch`) |
| `vibectl/` | local-only uv CLI wrapping flyctl (NOT in the Dockerfile; see "Local CLI" below) |
| `entrypoint.sh` | bash: boots `opencode serve` + `vm-tools watch`, writes state, `sleep infinity` |
| `skills/` | global OpenCode skills copied to `~/.config/opencode/skills` (`start-repo-work`, `gh-token`) |
| `home/AGENTS.md` | agent rules on the VM (session flow, ephemeral FS, push, tokens) |
| `home/opencode.jsonc` | global opencode config on the VM (auto-approve permissions) |

## Prereqs (local machine)

```bash
brew install flyctl        # or: curl -L https://fly.io/install.sh | sh
fly auth login
# opencode CLI locally as well (to connect with --server)
```

## One-time setup

```bash
cd ~/Documents/Code/fly-coding-vm

# 1. pick a unique app name and set it in fly.toml (app = "...")
APP=fly-coding-vm   # change if taken
fly apps create "$APP"

# 2. volume for /data (must exist before first deploy because of [mounts])
fly volumes create data --region iad --size 5 -a "$APP"

# 3. secrets (never committed; values are write-only on Fly's side)
OC_PW="$(openssl rand -hex 24)"
mkdir -p ~/.config/fly-coding-vm && printf '%s' "$OC_PW" > ~/.config/fly-coding-vm/opencode_password
fly secrets set -a "$APP" \
  OPENCODE_PASSWORD="$OC_PW" \
  OPENROUTER_API_KEY="sk-or-..." \
  GITHUB_APP_ID="123456" \
  GITHUB_APP_PRIVATE_KEY="$(cat ~/Downloads/fly-coding-vm-app.private-key.pem)"

# 4. build + deploy (machine starts running — stop it if you don't need it yet)
fly deploy -a "$APP"
fly machine stop "$(fly machine list -a "$APP" --json | jq -r '.[0].id')" -a "$APP"
```

Re-deploy after changing the Dockerfile, scripts, skills or `vm-tools/`:

```bash
fly deploy -a "$APP"     # then stop the machine again if needed
```

(Once `FLY_APP` is set, `vibectl deploy` / `vibectl stop` do the same thing.)

## Local CLI (vibectl)

`vibectl/` is a small **local-only** uv CLI (NOT installed in the Docker image). It
runs on the machine where `flyctl` lives, wraps the fly commands below, resolves
the machine id automatically, and owns the `fly proxy` child process — so you no
longer type the machine id or run `fly proxy ... &` by hand. Config comes
**only from environment variables**; vibectl never opens a `.env` file — you
`source` it yourself.

Install (from this repo):

```bash
uv tool install ./vibectl             # or: uvx --from ./vibectl vibectl --help
uv tool install --reinstall ./vibectl  # after editing vibectl/
```

Example `.env` (documented only — vibectl NEVER reads this file):

```bash
# .env
export FLY_APP=fly-coding-vm
export OPENCODE_PASSWORD="..."      # recover with: vibectl password
# export OC_PORT=4096               # optional local port; in-VM server listens on 4096
# export FLY_MACHINE_ID=...         # optional; auto-resolved from `fly machine list --json`
```

```bash
source .env
```

| Var | Required | Default | Used for |
|---|---|---|---|
| `FLY_APP` | yes | — | app name in every `fly -a ...` command |
| `FLY_MACHINE_ID` | no | auto (`fly machine list -a $FLY_APP --json` `[0].id`) | start/stop/status/exec/password |
| `OC_PORT` | no | `4096` | local proxy port + API base URL `http://127.0.0.1:$OC_PORT` |
| `OPENCODE_PASSWORD` | connect / prompt / sessions | — | Basic auth + passed to `opencode --server`; if missing, run `vibectl password` |

Commands — add `--dry-run` **before** the command to print the `fly`/`curl`
equivalents instead of running them (works without flyctl or a live app):

```bash
vibectl start             # start the machine + wait until started (id auto-resolved)
vibectl stop              # stop the machine
vibectl status            # fly machine status
vibectl logs              # fly logs -a $FLY_APP
vibectl logs --watcher    # tail -n 100 /data/logs/watcher.log via fly machine exec
vibectl exec -- <cmd>     # run one remote command (single string after --, NO shell)
vibectl password          # recover OPENCODE_PASSWORD from /data/state/opencode_password
vibectl connect           # start fly proxy + local `opencode --server`; kills proxy on exit
vibectl connect --web     # open the web UI instead (Ctrl-C stops the proxy); alias: vibectl proxy
vibectl prompt "..."      # create a session + POST /api/session/{id}/prompt through the proxy
vibectl sessions          # GET /api/session (add --json for raw output)
vibectl deploy            # fly deploy -a $FLY_APP
```

`vibectl exec -- ...` joins your argv into the single string `fly machine exec`
expects after `--` (it word-splits it and runs it **without a shell**). For
pipes/compound commands wrap it: `vibectl exec -- sh -c 'tail -n 100
/data/logs/watcher.log | grep stop'`. `vibectl connect` starts `fly proxy`, waits
for the local port, runs the TUI/web UI, and terminates the proxy when it exits.

## Daily usage

```bash
source .env                  # FLY_APP, OPENCODE_PASSWORD (vibectl reads env only)

vibectl start                  # start + wait; machine id resolved automatically
vibectl connect                # proxy + local TUI in ONE process; exiting the TUI kills the proxy
# ...or: vibectl connect --web  # opens http://127.0.0.1:4096 in a browser (Ctrl-C stops proxy)

# from another terminal (the proxy stays owned by `vibectl connect`):
vibectl prompt "Work on owner/repo: <task> (use the start-repo-work skill)"
vibectl sessions               # each entry has location.directory, time, cost, tokens
vibectl status
vibectl logs                   # or: vibectl logs --watcher
vibectl password               # recover the password if lost
vibectl exec -- sh -c 'tail -n 100 /data/logs/watcher.log | grep stop'

vibectl stop                   # manual stop (the watcher usually does this)
vibectl deploy                 # after changing Dockerfile/scripts/skills/vm-tools
```

The watcher stops the VM when all sessions have been idle for 15 min (or after
12 h max uptime); reading results between prompts does not stop it.

## Session flow (global session → delegated worker sessions)

1. You connect (TUI/web/API) and prompt the **global session** in `/root`.
2. The global session loads the **start-repo-work** skill — this is a
   **delegation** step, not where the work happens. It is the **only** session
   allowed to use that skill: `vm-tools start-work owner/repo` clones into
   `/root/work/<repo>`, then it creates a NEW **worker** session with
   `location.directory = /root/work/<repo>`, marks it (title `<repo> (worker)`),
   attaches a persistent `worker-no-delegate` instruction entry forbidding
   `start-repo-work` in worker sessions, and forwards the task prompt
   (`POST /api/session/{id}/prompt` with `{"text": ...}` that states the worker
   role and the no-delegation rule).
3. The global session replies with the new session ID and **does NOT wait or
   poll** — it continues its own work or disconnects. Results can be read any
   time via `GET /api/session/{id}/message`. If a session's directory is not
   `/root` (i.e. it lives under `/root/work/...`), it is a **worker** and must
   **refuse** to run `start-repo-work` — it just does the work. The repo's own
   `AGENTS.md`, `.opencode/skills`, etc. apply in the worker session; you can
   watch/steer it from your local client (session list shows all locations).
4. The watcher stops the VM when **no** session is running anywhere and none
   was updated within the idle window — so multiple repo sessions are fine and
   reading results between prompts does not stop the VM. "Delegate and let go"
   is safe: the worker keeps the VM alive while it runs.

## GitHub App setup (repo-scoped agent access)

1. **Create the app**: github.com → Settings → Developer settings → GitHub Apps →
   *New GitHub App* (or your org → Settings → Developer settings → GitHub Apps).
   - Name it e.g. `fly-coding-vm`. Disable the webhook (not needed).
   - **Repository permissions**: `Contents: Read and write`, `Metadata: Read`
     (auto). `Pull requests: Read and write` is **optional** (only if agents
     open PRs) — `vm-tools mint-gh-token` auto-intersects requested permissions
     with the installation's grants, so a minimal `Contents: write` +
     `Metadata: read` install works without it (no HTTP 422).
     No account/organization permissions.
   - *Where can this app be installed?* → **Only on this account** (or your org).
   - Save the **App ID**; click **Generate a private key** and download the `.pem`.
2. **Install on select repos only**: app page → *Install* → **Only select
   repositories** → pick the repos agents may touch → Install. (Avoid
   "all repositories" — it silently covers future repos.)
3. Put `GITHUB_APP_ID` + the PEM as Fly secrets (setup step 3). On the VM,
   `vm-tools start-work owner/repo` / `vm-tools mint-gh-token owner/repo --save`
   mint a 1 h token scoped to just that repo.

**Force pushes:** there is no per-app "no force push" toggle — use **branch
protection** on each repo: Settings → Branches → add rule for `main` → leave
*Allow force pushes* **unchecked** (blocked by default) and *Allow deletions*
unchecked; optionally enable *Do not allow bypassing the above settings* and use
*Restrict who can push* to name the app. The app bot is not a repo admin, so
the rule applies to it; normal (non-force) pushes and PR merges still work.
(Newer alternative: repository **rulesets**, same settings.)

## Cost notes

- Fly.io has **no spending limit / hard cap and no billing alerts** (per
  [Cost Management](https://docs.fly.io/about/cost-management): "free
  allowances don't cap your bill… We don't support billing alerts (yet)").
  Controls are: the watcher/self-stop (this repo), checking the dashboard's
  "current month to date" bill, and keeping the volume small.
- At shared-cpu-8x/8 GB in `iad`: ~$0.0744/h while running → roughly
  **$0.36–$1.79 per 8 h of coding** depending on idle handling; standing costs
  are the volume ($0.15/GB/mo → $0.75 for 5 GB) + stopped rootfs
  ($0.15/GB/mo). Region `fra` would be ×1.154 on compute.
