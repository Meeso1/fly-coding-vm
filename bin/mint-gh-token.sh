#!/usr/bin/env bash
# mint-gh-token.sh <owner/repo>
# Mints a 1-hour GitHub App installation access token scoped to ONE repo
# (via the `repositories` parameter of the access_tokens endpoint — GitHub
# itself enforces the boundary). Prints the token (ghs_...) to stdout.
#
# Required env (set as Fly secrets on the app):
#   GITHUB_APP_ID          - the app's ID
#   GITHUB_APP_PRIVATE_KEY - the app's PEM private key
set -euo pipefail

b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }

repo="${1:?usage: mint-gh-token.sh <owner/repo>}"
: "${GITHUB_APP_ID:?GITHUB_APP_ID not set}"
: "${GITHUB_APP_PRIVATE_KEY:?GITHUB_APP_PRIVATE_KEY not set}"

now=$(date +%s)
header=$(printf '{"alg":"RS256","typ":"JWT"}' | b64url)
payload=$(printf '{"iat":%d,"exp":%d,"iss":"%s"}' "$((now - 60))" "$((now + 540))" "$GITHUB_APP_ID" | b64url)
sig=$(printf '%s.%s' "$header" "$payload" \
  | openssl dgst -sha256 -sign <(printf '%s' "$GITHUB_APP_PRIVATE_KEY") | b64url)
jwt="${header}.${payload}.${sig}"

inst=$(curl -sf -H "Authorization: Bearer ${jwt}" -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "https://api.github.com/repos/${repo}/installation" | jq -r '.id')

curl -sf -X POST \
  -H "Authorization: Bearer ${jwt}" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -H "Content-Type: application/json" \
  -d "{\"repositories\":[\"${repo#*/}\"],\"permissions\":{\"contents\":\"write\",\"metadata\":\"read\",\"pull_requests\":\"write\"}}" \
  "https://api.github.com/app/installations/${inst}/access_tokens" \
  | jq -r '.token'
