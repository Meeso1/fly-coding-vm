"""Idle watcher: stop this Fly Machine when no OpenCode session is busy.

"Busy" = any session in ``GET /api/session/active`` is ``running``, OR any
session in ``GET /api/session`` (fully cursor-paginated) has ``time.updated``
within the idle window, OR a live *connect pin* exists (a ``vibectl connect``
client is attached) — so reading results between prompts does not stop the VM,
multiple sessions all count, and a connected client is never cut off.

Stop = POST to the in-guest Machines API unix socket ``/.fly/api``; compute is
billed per second, so billing stops when the machine reaches ``stopped``. Right
before stopping, one line is appended to ``metrics.jsonl`` capturing the total
OpenCode LLM cost observed for this VM run (start -> stop).

Fail-open: if the OpenCode API is unreachable/unparseable, the machine is
treated as busy (the max-uptime backstop still applies).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx

FLY_API_SOCKET = os.environ.get("FLY_API_SOCKET", "/.fly/api")
PIN_FILE = "connect-pin"
METRICS_FILE = "metrics.jsonl"


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


# --- connect pin -----------------------------------------------------------


def pin_valid(
    state_dir: Path, *, now: float | None = None, started: int | None = None
) -> bool:
    """True while a fresh (unexpired) connect pin exists on the volume.

    A pin is invalid when its expiry has passed, the file is missing/unreadable,
    or it predates the current boot (the machine was restarted) — checked via
    its mtime against the watcher's ``started`` timestamp. Expiry alone already
    makes stale/abandoned pins self-invalidating.
    """
    now = time.time() if now is None else now
    pin = state_dir / PIN_FILE
    try:
        expiry = float(pin.read_text().strip())
    except (OSError, ValueError):
        return False
    if expiry <= now:
        return False
    if started is not None:
        try:
            if pin.stat().st_mtime < started:
                return False
        except OSError:
            return False
    return True


def compute_busy(
    running: int, last_ms: int, now: float, idle_minutes: int, pinned: bool
) -> bool:
    """Busy = a running session, a recently-updated session, or a live pin."""
    if pinned or running > 0:
        return True
    return (now * 1000 - last_ms) < idle_minutes * 60 * 1000


# --- per-run LLM cost metrics ----------------------------------------------


def _as_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def session_costs(client: httpx.Client) -> tuple[float, list[dict]]:
    """Sum the per-session `cost` and return (total, per-session rows)."""
    total = 0.0
    rows: list[dict] = []
    for s in all_sessions(client):
        cost = _as_float(s.get("cost"))
        total += cost
        rows.append(
            {
                "id": s.get("id", ""),
                "title": s.get("title", ""),
                "cost": cost,
                "tokens": s.get("tokens"),
            }
        )
    return total, rows


def append_metrics(
    state_dir: Path,
    *,
    started: int,
    reason: str,
    llm_cost_usd: float,
    sessions: list[dict],
) -> None:
    """Append one JSON line describing this VM run (start -> stop)."""
    record = {
        "started_at": started,
        "stopped_at": int(time.time()),
        "reason": reason,
        "llm_cost_usd": round(llm_cost_usd, 6),
        "sessions": sessions,
    }
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        with (state_dir / METRICS_FILE).open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as exc:
        log(f"could not write metrics: {exc}")


def record_run_metrics(
    port: int, password: str, started: int, reason: str, state_dir: Path
) -> None:
    """Collect session costs and append a metrics line, right before stopping."""
    try:
        if password:
            with oc_client(port, password) as client:
                total, rows = session_costs(client)
        else:
            total, rows = 0.0, []
    except Exception as exc:
        log(f"could not collect session costs for metrics: {exc}")
        total, rows = 0.0, []
    append_metrics(
        state_dir, started=started, reason=reason, llm_cost_usd=total, sessions=rows
    )


# --- machine stop ----------------------------------------------------------


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
    was_pinned = False

    while True:
        now = time.time()
        if now - started >= max_uptime_hours * 3600:
            log(f"max uptime {max_uptime_hours}h reached")
            record_run_metrics(port, password, started, "max_uptime", state_dir)
            stop_machine()
            return 0

        pinned = pin_valid(state_dir, now=now, started=started)
        if pinned and not was_pinned:
            log("connect pin active; idle-stop suspended")
        elif was_pinned and not pinned:
            log("connect pin cleared")
        was_pinned = pinned

        busy = True  # fail-open default (also covers "no password" case)
        if password:
            try:
                with oc_client(port, password) as client:
                    running = active_count(client)
                    sessions = all_sessions(client)
                last_ms = last_updated_ms(sessions)
                busy = compute_busy(running, last_ms, now, idle_minutes, pinned)
            except Exception as exc:
                log(f"OpenCode API error (fail-open, treating as busy): {exc}")
        if pinned:
            busy = True

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
                record_run_metrics(port, password, started, "idle", state_dir)
                stop_machine()
                return 0

        time.sleep(poll_seconds)
