"""Mint GitHub App installation tokens scoped to a single repository.

The token is requested with the `repositories` parameter of the
access_tokens endpoint, so GitHub itself enforces the repo boundary.
Tokens expire after 1 hour.
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


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


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
    perms = permissions or DEFAULT_PERMISSIONS

    jwt = make_jwt(app_id, private_key)
    headers = {
        "Authorization": f"Bearer {jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
    }
    with httpx.Client(timeout=30) as client:
        resp = client.get(f"{API}/repos/{repo}/installation", headers=headers)
        resp.raise_for_status()
        installation_id = resp.json()["id"]

        resp = client.post(
            f"{API}/app/installations/{installation_id}/access_tokens",
            headers={**headers, "Content-Type": "application/json"},
            json={"repositories": [repo_name], "permissions": perms},
        )
        resp.raise_for_status()
        data = resp.json()
        # sanity: never hand out a token wider than requested
        assert data.get("repository_selection") in ("selected", None), data
        return data["token"]
