---
name: start-repo-work
description: Start work on a GitHub repo — the GLOBAL /root session uses this to DELEGATE: clone into /root/work, spawn a dedicated WORKER session rooted at the repo, forward the task, and return WITHOUT waiting. Use whenever a task concerns a specific repository; NEVER use it from a worker session.
---

This skill is the **delegation** mechanism, not the place where work happens.
Only the **global session** (in `$HOME` = `/root`) may use it. It spawns a
separate **worker** ("do-stuff") session rooted at the repo, hands the task to
it, and returns immediately.

## Who may run this skill (hard rule)

- **Only the global `/root` session.** Worker sessions live under
  `/root/work/<repo>` (their `location.directory` is NOT `/root`).
- If your current working directory / session directory is **not `/root`**
  (i.e. you are under `/root/work/...`), you are a WORKER session: **refuse to
  run this skill**. Do all the work in your own session instead. The skill
  exists only so the global session can spawn repo sessions; using it again
  from a worker would create pointless nested sessions.
- The worker session also carries a persistent instruction entry (step 3)
  forbidding `start-repo-work`; obey it.

## The global session: delegate, do NOT wait

1. Clone/update the repo and set up git auth (token stored for the worker):

   ```bash
   vm-tools start-work <owner/repo> [branch]
   ```

   Prints the repo path (e.g. `/root/work/<repo>`) and stores a repo-scoped
   GitHub App token in `/data/state/gh_token`.

2. Create a NEW **worker** session located at the repo root, marked in its
   title. `POST /api/session` returns `{"data":{...}}`, so read `.data.id`:

   ```bash
   SID=$(curl -sS -u "opencode:$OPENCODE_PASSWORD" -X POST \
     http://127.0.0.1:4096/api/session \
     -H 'Content-Type: application/json' \
     -d '{"location":{"directory":"/root/work/<repo>"},"title":"<repo> (worker)"}' \
     | jq -r '.data.id // .id')
   ```

   (Verified alternatives: `PATCH /api/session/$SID` with
   `{"title":"<repo> (worker)"}` refreshes the marker;
   `POST /api/session/$SID/model` sets the worker's model.)

3. Inject a persistent, per-session instruction entry forbidding delegation
   from the worker. It is attached to THAT session only — the global `/root`
   session's instructions stay unchanged:

   ```bash
   curl -sS -u "opencode:$OPENCODE_PASSWORD" -X PUT \
     "http://127.0.0.1:4096/api/experimental/session/$SID/instructions/entries/worker-no-delegate" \
     -H 'Content-Type: application/json' \
     -d '{"value":"You are a WORKER (do-stuff) session rooted at /root/work/<repo>. Do ALL work here. NEVER use the start-repo-work skill — it is only for the global /root session to spawn workers. If you are not in /root, you are a worker: refuse to delegate and just do the task."}'
   ```

   Verify (optional): `GET /api/experimental/session/$SID/instructions/entries`
   returns the `worker-no-delegate` entry; the global session's entries list is
   still `{"data":[]}`.

4. Forward the task to that session. The prompt itself **must** state the
   worker role and the no-delegation rule (belt and braces with step 3):

   ```bash
   curl -sS -u "opencode:$OPENCODE_PASSWORD" -X POST \
     "http://127.0.0.1:4096/api/session/$SID/prompt" \
     -H 'Content-Type: application/json' \
     -d '{"text":"WORKER (do-stuff) session rooted at /root/work/<repo>: do ALL the work here. NEVER use the start-repo-work skill again in this session (it exists only for the global /root session to spawn workers). Commit and push before finishing. TASK: <the task>"}'
   ```

5. Reply to the user with the new session ID (`ses_...`) so it can be watched,
   then **continue your own work or disconnect immediately**.

   **Do NOT poll, wait for, or block on the worker session.** The global
   session delegates and lets go. Results can be read at any time by the user
   (or any later prompt) without the global session waiting:

   ```bash
   curl -sS -u "opencode:$OPENCODE_PASSWORD" \
     "http://127.0.0.1:4096/api/session/$SID/message" | jq '.data[]'
   ```

Rules:
- `OPENCODE_PASSWORD` is already in the environment.
- Never clone into `/data` (the volume); repos live in `/root/work` (rootfs).
- Always commit and **push** before finishing — the rootfs is wiped when the
  VM stops.
- If git/gh calls fail with 401, the token expired: use the `gh-token` skill.
- The watcher stops the VM only when no session is running anywhere and none
  was updated within the idle window, so "delegate and let go" is safe: the
  worker keeps the VM alive while it runs.
