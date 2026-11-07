"""Mint GitHub App installation tokens scoped to a single repository.

The token is requested with the `repositories` parameter of the
access_tokens endpoint, so GitHub itself enforces the repo boundary.
Requested permissions are intersected with the permissions actually granted
to the installation (returned by GET /repos/{repo}/installation): GitHub
rejects (HTTP 422) any requested permission the installation does not grant
at >= the requested level, so a minimal `contents: write` + `metadata: read`
installation works without a `pull_requests` grant. Tokens expire after 1 hour.
"""

from __future__ import annotations

import base64
import json
import os
import time

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.hashes import SHA256

API = "https://api.github.com"
API_VERSION = "2022-11-28"

DEFAULT_PERMISSIONS = {
    "contents": "write",
    "metadata": "read",
    "pull_requests": "write",
}

_LEVELS = {"read": 0, "write": 1, "admin": 2}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def intersect_permissions(
    requested: dict[str, str], granted: dict[str, str]
) -> dict[str, str]:
    """Clamp ``requested`` permissions to those granted to the installation.

    Keys absent from ``granted`` are dropped; a requested level higher than the
    granted level is downgraded. GitHub returns HTTP 422 ("The permissions
    requested are not granted to this installation") for anything wider, so an
    installation that grants only ``contents: write`` + ``metadata: read`` must
    not be asked for ``pull_requests``.
    """
    out: dict[str, str] = {}
    for key, want in requested.items():
        have = granted.get(key)
        if have is None:
            continue
        out[key] = want if _LEVELS.get(want, 0) <= _LEVELS.get(have, 0) else have
    return out


def make_jwt(app_id: str, private_key_pem: str, *, lifetime_s: int = 540) -> str:
    """Sign a GitHub App JWT (RS256). Max allowed lifetime is 10 minutes."""
    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = _b64url(
        json.dumps({"iat": now - 60, "exp": now + lifetime_s, "iss": app_id}).encode()
    )
    signature = key.sign(f"{header}.{payload}".encode(), padding.PKCS1v15(), SHA256())
    return f"{header}.{payload}.{_b64url(signature)}"


def mint_token(
    repo: str,
    *,
    app_id: str | None = None,
    private_key: str | None = None,
    permissions: dict | None = None,
) -> str:
    """Return a 1h installation access token (ghs_...) for owner/repo only."""
    app_id = app_id or os.environ["GITHUB_APP_ID"]
    private_key = private_key or os.environ["GITHUB_APP_PRIVATE_KEY"]
    repo_name = repo.split("/", 1)[1]
    wanted = DEFAULT_PERMISSIONS if permissions is None else permissions

    jwt = make_jwt(app_id, private_key)
    headers = {
        "Authorization": f"Bearer {jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
    }
    with httpx.Client(timeout=30) as client:
        resp = client.get(f"{API}/repos/{repo}/installation", headers=headers)
        resp.raise_for_status()
        installation = resp.json()
        installation_id = installation["id"]
        granted = installation.get("permissions") or {}
        perms = intersect_permissions(wanted, granted)

        payload = {"repositories": [repo_name]}
        if perms:
            payload["permissions"] = perms
        resp = client.post(
            f"{API}/app/installations/{installation_id}/access_tokens",
            headers={**headers, "Content-Type": "application/json"},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        # sanity: never hand out a token wider than requested
        assert data.get("repository_selection") in ("selected", None), data
        return data["token"]
