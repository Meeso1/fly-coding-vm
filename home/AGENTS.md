# Agent rules on this VM

- This VM is **ephemeral**: the root filesystem is wiped when the machine
  stops. The only persistent storage is the `/data` volume — do NOT clone
  repos there.
- To work on a repo, run `start-work.sh <owner/repo> [branch]`. It clones into
  `/root/work/<repo>` (inside this project directory, so all tools work
  there), sets up git auth, and prints the directory. Work there.
- In each new shell that needs the GitHub CLI:
  `export GITHUB_TOKEN="$(cat /data/state/gh_token)"` (token expires after 1 h;
  re-run `mint-gh-token.sh <owner/repo>` if it expired).
- Always commit and **push** your work before finishing — unpushed work is
  lost when the machine stops.
- Do NOT stop or shut down this machine yourself. A watcher stops it
  automatically when all OpenCode sessions have been idle for a while (and
  after a max uptime regardless). Just finish, push, and summarize.
- Keep scratch files and logs in `/tmp` or `/data/logs`, not in repos.
