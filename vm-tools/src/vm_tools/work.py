"""Clone/update a repo into the VM work dir and wire up git auth."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .github_app import mint_token

CREDENTIAL_HELPER = (
    '!f() { echo "username=x-access-token"; echo "password=$GITHUB_TOKEN"; }; f'
)


def _git(*args: str, cwd: Path | None = None, check: bool = True) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=check)


def start_work(
    repo: str,
    branch: str | None = None,
    *,
    work_dir: Path | None = None,
    state_dir: Path | None = None,
) -> Path:
    """Clone (or update) owner/repo into <work_dir>/<repo> and return the path.

    Credentials: a repo-scoped GitHub App token is minted, used for the initial
    clone (then stripped from the remote URL), stored with mode 0600 in
    <state_dir>/gh_token, and a credential helper reading $GITHUB_TOKEN is set
    in the repo's local git config.
    """
    work_dir = work_dir or Path(os.environ.get("VM_WORK_DIR", "/root/work"))
    state_dir = state_dir or Path(os.environ.get("VM_STATE_DIR", "/data/state"))
    work_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)

    token = mint_token(repo)
    name = repo.split("/")[-1]
    dest = work_dir / name

    if (dest / ".git").exists():
        _git("-C", str(dest), "fetch", "origin", "--prune")
    else:
        _git(
            "clone",
            f"https://x-access-token:{token}@github.com/{repo}.git",
            str(dest),
        )
        # never leave the token in the stored remote URL
        _git("-C", str(dest), "remote", "set-url", "origin", f"https://github.com/{repo}.git")

    _git("-C", str(dest), "config", "credential.helper", CREDENTIAL_HELPER)
    if branch:
        _git("-C", str(dest), "checkout", branch)
    _git("-C", str(dest), "pull", "--ff-only", check=False)

    token_file = state_dir / "gh_token"
    token_file.write_text(token)
    token_file.chmod(0o600)
    return dest


def save_token(repo: str, *, state_dir: Path | None = None) -> Path:
    """Mint a fresh repo-scoped token and store it (0600) in the state dir."""
    state_dir = state_dir or Path(os.environ.get("VM_STATE_DIR", "/data/state"))
    state_dir.mkdir(parents=True, exist_ok=True)
    token = mint_token(repo)
    token_file = state_dir / "gh_token"
    token_file.write_text(token)
    token_file.chmod(0o600)
    return token_file
