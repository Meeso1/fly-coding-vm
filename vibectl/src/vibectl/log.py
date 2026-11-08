"""File-based activity log for vibectl.

Every fly command, OpenCode API call, exit code and important event is appended
to ``logs/vibectl-<app>.log`` in the directory vibectl was invoked from (next
to ``<app>.env`` / ``<app>.port``). Logging is additive: it never changes
stdout/stderr and never raises on failure. ``OPENCODE_PASSWORD`` and
token-looking strings are redacted before writing. Set ``VIBECTL_LOG_DIR`` to
override the ``logs/`` directory (useful for tests).
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

_REDACTED = "<redacted>"

# Whole ``KEY=value`` tokens for secret env vars. shlex.quote wraps tokens that
# contain spaces/newlines in single quotes, so match the FULL quoted token (a
# bare ``\S+`` alternative would leave the rest of a PEM body behind).
_SECRET_KEYS = (
    "OPENCODE_PASSWORD|OPENROUTER_API_KEY|GITHUB_APP_PRIVATE_KEY|"
    "GITHUB_APP_ID|GITHUB_TOKEN"
)
_SECRET_TOKEN = re.compile(
    rf"'(?P<k1>{_SECRET_KEYS})=[^']*'"
    rf"|\"(?P<k2>{_SECRET_KEYS})=[^\"]*\""
    rf"|(?P<k3>{_SECRET_KEYS})=\S+"
)


def _redact_assignment(m: re.Match) -> str:
    key = m.group("k1") or m.group("k2") or m.group("k3")
    return f"{key}={_REDACTED}"

# Common token shapes (GitHub App/installation tokens, OpenRouter keys, JWTs).
_TOKEN = re.compile(
    r"(?:gh[sopru]_[A-Za-z0-9]{16,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|sk-or-[A-Za-z0-9\-]{16,}"
    r"|sk-[A-Za-z0-9]{20,}"
    r"|eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,})"
)

_SECRET_ENV_NAMES = (
    "OPENCODE_PASSWORD",
    "OPENROUTER_API_KEY",
    "GITHUB_APP_PRIVATE_KEY",
    "GITHUB_TOKEN",
)


def _env_secrets() -> set[str]:
    return {
        v for name in _SECRET_ENV_NAMES
        if (v := os.environ.get(name)) and len(v) >= 4
    }


def default_logs_dir() -> Path:
    """The ``logs/`` dir in the invoking folder (or ``$VIBECTL_LOG_DIR``)."""
    override = os.environ.get("VIBECTL_LOG_DIR")
    return Path(override) if override else Path.cwd() / "logs"


class ActivityLog:
    """Append-only, best-effort activity log (a disabled instance is a no-op)."""

    def __init__(
        self,
        app: str,
        dry_run: bool = False,
        *,
        logs_dir: Path | str | None = None,
        enabled: bool = True,
    ) -> None:
        self.app = app
        self.dry_run = dry_run
        self._enabled = enabled
        self.path: Path | None = (
            Path(logs_dir or default_logs_dir()) / f"vibectl-{app}.log"
            if enabled
            else None
        )
        self._secrets = _env_secrets()

    def add_secret(self, value: str | None) -> None:
        if value and len(value) >= 4:
            self._secrets.add(value)

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, _REDACTED)
        text = _SECRET_TOKEN.sub(_redact_assignment, text)
        text = _TOKEN.sub(_REDACTED, text)
        return text

    def write(self, kind: str, message: str) -> None:
        """Append one timestamped (and redacted) line; never raises."""
        if not self._enabled or self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            tag = "DRY" if self.dry_run else "RUN"
            line = f"{ts} [{tag}] {kind}: {self.redact(message)}\n"
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)
        except OSError:
            pass  # logging must never break a command

    def event(self, name: str, **fields: object) -> None:
        detail = " ".join(
            f"{key}={value}" for key, value in fields.items() if value is not None
        )
        self.write("event", f"{name} {detail}".strip())
