"""Environment-only configuration for vmctl.

Config is read exclusively from ``os.environ``. vmctl NEVER opens a ``.env``
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
    port: int
    password: str | None
    dry_run: bool


_ENV_HINT = (
    "Set the required env vars first, e.g.:\n"
    "  source .env          # FLY_APP, OPENCODE_PASSWORD, OC_PORT, ...\n"
    "vmctl reads config ONLY from the environment; it never opens a .env file."
)


def _require(name: str, extra: str = "") -> str:
    value = os.environ.get(name)
    if not value:
        message = f"{name} is not set.\n{_ENV_HINT}"
        if extra:
            message += "\n" + extra
        raise ConfigError(message)
    return value


def load(dry_run: bool = False, *, need_password: bool = False) -> Config:
    """Build a :class:`Config` from ``os.environ`` only.

    ``FLY_APP`` is always required. ``OPENCODE_PASSWORD`` is required for the
    client/API commands in real (non-dry-run) mode; in ``--dry-run`` its value
    is never used (only the literal ``$OPENCODE_PASSWORD`` is printed), so it is
    not required there.
    """
    app = _require("FLY_APP")
    raw_port = os.environ.get("OC_PORT", "4096")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ConfigError(f"OC_PORT must be an integer, got {raw_port!r}.") from exc
    machine_id = os.environ.get("FLY_MACHINE_ID") or None

    password: str | None = None
    if need_password and not dry_run:
        password = _require(
            "OPENCODE_PASSWORD",
            extra=(
                "Lost it? Run `vmctl password` to recover it from "
                "/data/state/opencode_password, then export it (or source .env)."
            ),
        )
    return Config(
        app=app,
        machine_id=machine_id,
        port=port,
        password=password,
        dry_run=dry_run,
    )


def require_fly(dry_run: bool = False) -> None:
    """Fail clearly when ``fly`` is not on PATH (skipped in ``--dry-run``)."""
    if dry_run:
        return
    if shutil.which("fly") is None:
        raise ConfigError(
            "fly (flyctl) not found on PATH. Install it (e.g. `brew install flyctl`) "
            "and run `fly auth login`. vmctl is a LOCAL tool that drives flyctl."
        )
