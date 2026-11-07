# fly-coding-vm — remaining setup checklist (temp, not committed)

Repo state: all image/config files are done and committed (`fly-coding-vm` @ `b42e014`, clean tree).
What's left is external setup (GitHub App, Fly account) + first-deploy verification.

## A. GitHub side (once)

1. Create the GitHub App: github.com → Settings → Developer settings → GitHub Apps → New.
   - Name e.g. `fly-coding-vm`; disable webhook.
   - Repository permissions: `Contents: Read and write`, `Metadata: Read` (auto),
     `Pull requests: Read and write` if agents should open PRs. No account/org permissions.
   - Installable on: **Only on this account** (or your org).
   - Save the **App ID**; generate + download the private key `.pem`.
2. Install on select repos only: app page → Install → *Only select repositories* → pick repos. (Not "all repositories".)
3. Branch protection per repo (force-push guard): Settings → Branches → rule for `main` →
   leave *Allow force pushes* / *Allow deletions* unchecked (applies to the app bot too);
   optionally *Do not allow bypassing* + *Restrict who can push* → add the app.
   Normal pushes/PRs unaffected.

## B. Fly.io side (once)

4. Local: install `flyctl`, `fly auth login`.
5. Edit `fly.toml`: unique `app = "..."`, region (`iad` cheapest; `fra` ×1.154 on compute);
   optional `[env]` overrides: `IDLE_MINUTES` (15), `MAX_UPTIME_HOURS` (12), `OC_PORT` (4096).
6. `fly apps create <app>`
7. `fly volumes create data --region <region> --size 5 -a <app>` — must exist before first deploy (`[mounts]`).
8. Secrets (write-only on Fly — save the password locally too):
   ```bash
   OC_PW="$(openssl rand -hex 24)"
   mkdir -p ~/.config/fly-coding-vm && printf '%s' "$OC_PW" > ~/.config/fly-coding-vm/opencode_password
   fly secrets set -a <app> OPENCODE_PASSWORD="$OC_PW" OPENROUTER_API_KEY="sk-or-..." \
     GITHUB_APP_ID="<app id>" GITHUB_APP_PRIVATE_KEY="$(cat <key>.pem)"
   ```
   Also make sure the model provider is configured on the VM (env key is picked up
   automatically, e.g. OpenRouter via `OPENROUTER_API_KEY`).

## C. First deploy

9. `fly deploy -a <app>` (Depot build is free; machine starts).
10. Stop it if not needed yet:
    `fly machine stop "$(fly machine list -a <app> --json | jq -r '.[0].id')" -a <app>`
    (Every `fly deploy` starts the machine — stop it after each deploy if idle.)

## D. Verification (first real run)

11. `fly machine start <id> -a <app>`; `fly machine wait <id> -a <app> --state started`.
12. Health: `fly logs -a <app>`; `fly machine exec <id> -a <app> -- tail /data/logs/opencode-serve.log`;
    `... -- tail /data/logs/watcher.log`; `... -- vm-tools --version` (uv install + PATH check).
13. Connect: `fly proxy 4096:4096 -a <app> &` then
    `OPENCODE_PASSWORD=$(cat ~/.config/fly-coding-vm/opencode_password) opencode --server http://127.0.0.1:4096`
    — global session should be in `/root`. Web UI at the same address. (`fly doctor` if proxy fails.)
14. Token path: prompt global session to run `vm-tools mint-gh-token <owner>/<installed-repo> --save` → expect `ghs_...`.
15. End-to-end on a scratch repo: prompt "work on owner/repo: <tiny task> (use start-repo-work)" →
    agent runs `vm-tools start-work`, creates session at `/root/work/<repo>`, works, pushes.
    Watch via `GET /api/session` (has `location.directory`, `time`, `cost`, `tokens` per session).
16. Self-stop: idle ~15 min → `fly machine status` shows `stopped` (watcher.log has the stop call).
    Restart and confirm opencode session history survived via the `/data` symlink.
17. Manual stop: `fly machine stop <id> -a <app>`.

## E. Daily loop

18. start + wait → `fly proxy 4096:4096 -a <app> &` → connect TUI/web → prompt global `/root`
    session → watcher stops the VM when all sessions idle.
    Expected cost: ~$0.36–$1.79 per 8 h coding (shared-cpu-8x/8 GB, `iad`) + ~$0.75/mo volume
    + ~$0.15/GB/mo stopped rootfs. Fly has NO spending cap/alerts — check the dashboard's
    month-to-date figure occasionally.

## F. Optional / later

19. Push this repo to GitHub (self-contained now, no submodule).
20. Tighten `home/opencode.jsonc` with `policies` denies (e.g. `shell:git push --force *`)
    as a second layer on top of branch protection.
21. Usage limits (deliberately skipped): per-session `cost`/`tokens` via `GET /api/session`;
    hard general cap = provider API key spend limit (e.g. OpenRouter).

Blocking items: A1–A3 + B6–B8 (GitHub App secrets + volume) — first deploy/session fails without them.
