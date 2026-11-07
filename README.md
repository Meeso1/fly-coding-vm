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

## Daily usage

```bash
APP=fly-coding-vm
MID=$(fly machine list -a "$APP" --json | jq -r '.[0].id')

# start the VM and wait for it (cold start ~ seconds)
fly machine start "$MID" -a "$APP"
fly machine wait "$MID" -a "$APP" --state started

# forward the opencode server port over WireGuard (run in background)
fly proxy 4096:4096 -a "$APP" &

# connect with the local TUI — default location is the global $HOME session
OC_PW=$(cat ~/.config/fly-coding-vm/opencode_password)
OPENCODE_PASSWORD="$OC_PW" opencode --server http://127.0.0.1:4096
# ...or open the web UI in a browser: http://127.0.0.1:4096 (password = $OC_PW)
# (password recovery if lost: fly machine exec "$MID" -a "$APP" -- cat /data/state/opencode_password)

# queue a prompt from a script instead of the TUI:
SID=$(curl -sS -u "opencode:$OC_PW" -X POST http://127.0.0.1:4096/api/session \
  -H 'Content-Type: application/json' \
  -d '{"location":{"directory":"/root"},"title":"orchestration"}' | jq -r '.id')
curl -sS -u "opencode:$OC_PW" -X POST "http://127.0.0.1:4096/api/session/$SID/prompt" \
  -H 'Content-Type: application/json' \
  -d '{"text":"Work on owner/repo: <task> (use the start-repo-work skill)"}'
# watch any session: GET /api/session  (each entry has location.directory, time, cost, tokens)

# inspect
fly machine status "$MID" -a "$APP"
fly logs -a "$APP"
fly machine exec "$MID" -a "$APP" -- tail -n 100 /data/logs/watcher.log
# note on `fly machine exec`: the remote command is ONE string after `--`,
# word-split into argv and executed WITHOUT a shell (no pipes/redirects/`;`).
# Missing `--` => "invalid number of arguments". Compound commands:
#   fly machine exec "$MID" -a "$APP" -- "sh -c 'ps aux | grep opencode'"

# stop manually (the watcher usually does this: 15 min idle or 12 h max uptime)
fly machine stop "$MID" -a "$APP"
```

## Session flow (global session → repo-root sessions)

1. You connect (TUI/web/API) and prompt the **global session** in `/root`.
2. The agent loads the **start-repo-work** skill: `vm-tools start-work
   owner/repo` clones into `/root/work/<repo>`, then the skill creates a new
   session with `location.directory = /root/work/<repo>` and forwards the task
   prompt to it (`POST /api/session/{id}/prompt` with `{"text": ...}`).
3. The repo-root session does the work — the repo's own `AGENTS.md`,
   `.opencode/skills`, etc. apply there. You can watch/steer it from your local
   client (session list shows all locations).
4. The watcher stops the VM when **no** session is running anywhere and none
   was updated within the idle window — so multiple repo sessions are fine and
   reading results between prompts does not stop the VM.

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
