"""Idle watcher: stop this Fly Machine when no OpenCode session is busy.

"Busy" = any session in ``GET /api/session/active`` is ``running``, OR any
session in ``GET /api/session`` (fully cursor-paginated) has ``time.updated``
within the idle window — so reading results between prompts does not stop the
VM, and multiple sessions in different repos all count.

Stop = POST to the in-guest Machines API unix socket ``/.fly/api``; compute is
billed per second, so billing stops when the machine reaches ``stopped``.

Fail-open: if the OpenCode API is unreachable/unparseable, the machine is
treated as busy (the max-uptime backstop still applies).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

FLY_API_SOCKET = os.environ.get("FLY_API_SOCKET", "/.fly/api")


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} watcher: {msg}", flush=True)


def oc_client(port: int, password: str) -> httpx.Client:
    return httpx.Client(
        base_url=f"http://127.0.0.1:{port}",
        auth=("opencode", password),
        timeout=10,
    )


def active_count(client: httpx.Client) -> int:
    """Number of currently running sessions (GET /api/session/active)."""
    r = client.get("/api/session/active")
    r.raise_for_status()
    return len(r.json().get("data") or {})


def all_sessions(client: httpx.Client, page_size: int = 200) -> list[dict]:
    """GET /api/session with full cursor pagination (follows cursor.next)."""
    sessions: list[dict] = []
    cursor: str | None = None
    while True:
        params: dict[str, str] = {"limit": str(page_size)}
        if cursor:
            params["cursor"] = cursor
        r = client.get("/api/session", params=params)
        r.raise_for_status()
        body = r.json()
        sessions.extend(body.get("data") or [])
        cursor = (body.get("cursor") or {}).get("next")
        if not cursor:
            return sessions


def last_updated_ms(sessions: list[dict]) -> int:
    """Newest session time.updated (epoch ms) across all sessions."""
    return max((s.get("time", {}).get("updated", 0) for s in sessions), default=0)


def stop_machine() -> None:
    """Stop this machine via the in-guest Machines API unix socket."""
    app = os.environ.get("FLY_APP_NAME", "")
    machine = os.environ.get("FLY_MACHINE_ID", "")
    transport = httpx.HTTPTransport(uds=FLY_API_SOCKET)
    with httpx.Client(transport=transport, base_url="http://flaps", timeout=10) as client:
        r = client.post(f"/v1/apps/{app}/machines/{machine}/stop")
    log(f"stop request via {FLY_API_SOCKET} -> HTTP {r.status_code}")


def run(
    *,
    idle_minutes: int = 15,
    max_uptime_hours: int = 12,
    port: int = 4096,
    poll_seconds: int = 30,
    state_dir: Path | None = None,
    password: str | None = None,
) -> int:
    state_dir = state_dir or Path(os.environ.get("VM_STATE_DIR", "/data/state"))
    if password is None:
        password = os.environ.get("OPENCODE_PASSWORD", "")

    started_file = state_dir / "started_at"
    try:
        started = int(started_file.read_text().strip())
    except Exception:
        started = int(time.time())
        try:
            state_dir.mkdir(parents=True, exist_ok=True)
            started_file.write_text(str(started))
        except OSError:
            pass

    log(
        f"watching OpenCode on :{port} "
        f"(idle={idle_minutes} min, max uptime={max_uptime_hours} h, poll={poll_seconds} s)"
    )
    idle_since: float | None = None

    while True:
        now = time.time()
        if now - started >= max_uptime_hours * 3600:
            log(f"max uptime {max_uptime_hours}h reached")
            stop_machine()
            return 0

        busy = True  # fail-open default
        if password:
            try:
                with oc_client(port, password) as client:
                    running = active_count(client)
                    sessions = all_sessions(client)
                last_ms = last_updated_ms(sessions)
                busy = running > 0 or (now * 1000 - last_ms) < idle_minutes * 60 * 1000
            except Exception as exc:
                log(f"OpenCode API error (fail-open, treating as busy): {exc}")

        if busy:
            if idle_since is not None:
                log("activity detected; idle timer reset")
            idle_since = None
        else:
            if idle_since is None:
                idle_since = now
                log(
                    "no running sessions, none updated recently; "
                    f"idle timer started ({idle_minutes} min)"
                )
            elif now - idle_since >= idle_minutes * 60:
                log(f"idle for {idle_minutes} min")
                stop_machine()
                return 0

        time.sleep(poll_seconds)
