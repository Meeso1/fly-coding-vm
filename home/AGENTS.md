# Agent rules on this VM

## Sessions and locations
- The **global session** lives in `$HOME` (`/root`) — that is where prompts
  arrive. When a task is about a specific GitHub repo, use the
  **start-repo-work** skill: it clones the repo into `/root/work/<repo>` and
  creates a dedicated OpenCode session rooted at the repo, so the repo's own
  `AGENTS.md` and `.opencode/skills` apply. Do the actual work in that
  repo-root session.
- VM-wide skills (`start-repo-work`, `gh-token`) are global:
  `~/.config/opencode/skills/`.

## Ephemeral machine
- The rootfs is wiped when the machine stops; only `/data` (volume) persists.
  Never clone repos into `/data`. Repos live in `/root/work` and are always
  pushed to GitHub before finishing — unpushed work is lost on stop.
- A watcher stops the VM automatically when no session is running and none was
  updated for 15 minutes (and after 12 h max uptime regardless). Do NOT stop
  the machine yourself; just finish, push, and summarize.

## Task summaries
- When a task is finished, `mkdir -p /data/summaries` and write a SHORT
  high-level summary (2-5 lines) to `/data/summaries/<session-id>.md`. Highlight
  non-obvious decisions and gotchas, not a list of code changes. `/data` persists
  across VM stop/start.

## GitHub auth
- Access is via repo-scoped GitHub App tokens (valid 1 h).
  `vm-tools start-work` stores one in `/data/state/gh_token`; git in the repo
  uses a credential helper that reads `$GITHUB_TOKEN` from the environment.
  For the `gh` CLI: `export GITHUB_TOKEN="$(cat /data/state/gh_token)"`.
  On 401s, refresh with the **gh-token** skill.
