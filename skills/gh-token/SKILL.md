---
name: gh-token
description: Mint a fresh repo-scoped GitHub App token (valid 1h) and store it in /data/state/gh_token. Use when git push or gh calls fail with 401 (expired token).
---

```bash
vm-tools mint-gh-token <owner/repo> --save
```

Then in any shell that needs the GitHub CLI:

```bash
export GITHUB_TOKEN="$(cat /data/state/gh_token)"
```

(git inside `/root/work/<repo>` already has a credential helper reading
`$GITHUB_TOKEN` from the environment.)
