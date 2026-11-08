"""Environment-only configuration for vibectl.

Config is read exclusively from ``os.environ``. vibectl NEVER opens a ``.env``
file: the user sources it in their shell first. A missing required var raises a
friendly :class:`ConfigError` that tells the user to ``source .env``.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass


class ConfigError(Exception):
    """A friendly, user-facing configuration problem (CLI exits with code 2)."""


@dataclass
class Config:
    app: str
    machine_id: str | None
    port: int | None  # OC_PORT override; None -> auto-picked free port (see fly.py)
    password: str | None
    dry_run: bool
    region: str | None = None  # FLY_REGION (written into the env file by init)


_ENV_HINT = (
    "vibectl reads config ONLY from the environment; it never opens a .env file.\n"
    "Source the env file written by `vibectl init` first, e.g.:\n"
    "  source <app>.env        # exports FLY_APP + OPENCODE_PASSWORD"
)


def _require(name: str, extra: str = "") -> str:
    value = os.environ.get(name)
    if not value:
        message = f"{name} is not set.\n{_ENV_HINT}"
        if extra:
            message += "\n" + extra
        raise ConfigError(message)
    return value


def load(
    dry_run: bool = False,
    *,
    need_password: bool = False,
    app: str | None = None,
) -> Config:
    """Build a :class:`Config` from ``os.environ`` only.

    ``FLY_APP`` is always required — except when the caller supplies ``app``
    directly (``vibectl init <app>`` takes the app from its CLI argument, so no
    sourced env file is needed there yet). ``OPENCODE_PASSWORD`` is required for
    the client/API commands in real (non-dry-run) mode; in ``--dry-run`` its
    value is never used (only the literal ``$OPENCODE_PASSWORD`` is printed), so
    it is not required there. ``OC_PORT`` is an OPTIONAL override for the local
    proxy port (unset -> auto-picked free port); ``FLY_REGION`` carries the
    per-app deploy region that ``vibectl init`` wrote into the env file.
    """
    app = app or _require("FLY_APP")
    raw_port = os.environ.get("OC_PORT")
    port: int | None = None
    if raw_port is not None:
        try:
            port = int(raw_port)
        except ValueError as exc:
            raise ConfigError(f"OC_PORT must be an integer, got {raw_port!r}.") from exc
    machine_id = os.environ.get("FLY_MACHINE_ID") or None
    region = os.environ.get("FLY_REGION") or None

    password: str | None = None
    if need_password and not dry_run:
        password = _require(
            "OPENCODE_PASSWORD",
            extra=(
                "Lost it? Run `vibectl password` to recover it from "
                "/data/state/opencode_password, then export it (or put it in the env "
                "file written by `vibectl init`)."
            ),
        )
    return Config(
        app=app,
        machine_id=machine_id,
        port=port,
        password=password,
        dry_run=dry_run,
        region=region,
    )


def require_fly(dry_run: bool = False) -> None:
    """Fail clearly when ``fly`` is not on PATH (skipped in ``--dry-run``)."""
    if dry_run:
        return
    if shutil.which("fly") is None:
        raise ConfigError(
            "fly (flyctl) not found on PATH. Install it (e.g. `brew install flyctl`) "
            "and run `fly auth login`. vibectl is a LOCAL tool that drives flyctl."
        )
