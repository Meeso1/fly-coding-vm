---
name: start-repo-work
description: Start work on a GitHub repo — clone it into /root/work and open a dedicated OpenCode session rooted at the repo (so its AGENTS.md and .opencode/skills apply), then forward the task there. Use whenever a task concerns a specific repository.
---

1. Clone/update the repo and set up git auth:

   ```bash
   vm-tools start-work <owner/repo> [branch]
   ```

   Prints the repo path (e.g. `/root/work/<repo>`) and stores a repo-scoped
   GitHub App token in `/data/state/gh_token`.

2. Create a NEW session located at the repo root (do NOT move the global
   $HOME session — it stays the entrypoint for orchestration):

   ```bash
   SID=$(curl -sS -u "opencode:$OPENCODE_PASSWORD" -X POST http://127.0.0.1:4096/api/session \
     -H 'Content-Type: application/json' \
     -d '{"location":{"directory":"/root/work/<repo>"},"title":"<repo>"}' | jq -r '.id')
   ```

3. Forward the task to that session:

   ```bash
   curl -sS -u "opencode:$OPENCODE_PASSWORD" -X POST \
     "http://127.0.0.1:4096/api/session/$SID/prompt" \
     -H 'Content-Type: application/json' \
     -d '{"text":"<the task>"}'
   ```

4. Reply with the new session ID (`ses_...`) so it can be watched in the
   TUI/web session list.

Rules:
- `OPENCODE_PASSWORD` is already in the environment.
- Never clone into `/data` (the volume); repos live in `/root/work` (rootfs).
- Always commit and **push** before finishing — the rootfs is wiped when the
  VM stops.
- If git/gh calls fail with 401, the token expired: use the `gh-token` skill.
