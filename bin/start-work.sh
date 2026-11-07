#!/usr/bin/env bash
# start-work.sh <owner/repo> [branch]
# Clones (or updates) the repo into /root/work/<repo> — inside the $HOME
# OpenCode project, so all file tools work there — and configures git auth
# with a freshly minted repo-scoped GitHub App token.
set -euo pipefail

repo="${1:?usage: start-work.sh <owner/repo> [branch]}"
branch="${2:-}"
name="${repo##*/}"
dir="/root/work/${name}"

token="$(mint-gh-token.sh "$repo")"

mkdir -p /root/work
if [ -d "$dir/.git" ]; then
  git -C "$dir" fetch origin --prune
else
  git clone "https://x-access-token:${token}@github.com/${repo}.git" "$dir"
  # don't leave the token in the remote URL
  git -C "$dir" remote set-url origin "https://github.com/${repo}.git"
fi

# credential helper reads $GITHUB_TOKEN from the environment at call time
git -C "$dir" config credential.helper '!f() { echo "username=x-access-token"; echo "password=$GITHUB_TOKEN"; }; f'

if [ -n "$branch" ]; then git -C "$dir" checkout "$branch"; fi
git -C "$dir" pull --ff-only || true

umask 077
printf '%s' "$token" > /data/state/gh_token

echo "repo ready: $dir"
echo "for gh CLI in this shell: export GITHUB_TOKEN=\"\$(cat /data/state/gh_token)\""
