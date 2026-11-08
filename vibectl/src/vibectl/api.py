"""OpenCode HTTP API calls through the local fly proxy (stdlib urllib only)."""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

from .config import ConfigError
from .fly import Runner, proxy_up


def _base(r: Runner) -> str:
    return f"http://127.0.0.1:{r.cfg.port}"


def _auth(password: str) -> str:
    token = base64.b64encode(f"opencode:{password}".encode()).decode()
    return f"Basic {token}"


def _curl(r: Runner, method: str, path: str, body: dict | None) -> str:
    """Render the equivalent curl command for ``--dry-run`` (never leak the PW)."""
    url = f"{_base(r)}{path}"
    out = f'curl -sS -u "opencode:$OPENCODE_PASSWORD" -X {method} {url}'
    if body is not None:
        out += (
            " -H 'Content-Type: application/json' -d '"
            + json.dumps(body, separators=(",", ":"))
            + "'"
        )
    return out


def _as_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def request(r: Runner, method: str, path: str, *, body: dict | None = None) -> dict:
    """Perform an API call, or print its curl equivalent in ``--dry-run``."""
    if r.cfg.dry_run:
        print("[dry-run] " + _curl(r, method, path, body))
        r.log.write("api", f"{method} {path} (dry-run) :: {_curl(r, method, path, body)}")
        return {}
    url = f"{_base(r)}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": _auth(r.cfg.password or "")}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    r.log.write(
        "api", f"{method} {path} request body={json.dumps(body) if body is not None else ''}"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            status = resp.status
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:300]
        r.log.write("api", f"{method} {path} -> HTTP {exc.code}: {detail}")
        raise ConfigError(
            f"OpenCode API {method} {path} -> HTTP {exc.code}: {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        r.log.write("api", f"{method} {path} -> unreachable: {exc.reason}")
        raise ConfigError(
            f"OpenCode API unreachable at {url} ({exc.reason}); "
            "is `vibectl connect` / the proxy running?"
        ) from exc
    r.log.write("api", f"{method} {path} -> HTTP {status} ({len(raw)} bytes): {raw[:400]}")
    return json.loads(raw) if raw.strip() else {}


def create_session(r: Runner, *, title: str, directory: str) -> str:
    """POST /api/session and return the new session id."""
    body = {"location": {"directory": directory}, "title": title}
    data = request(r, "POST", "/api/session", body=body)
    if r.cfg.dry_run:
        return "<session-id>"
    sid = None
    if isinstance(data, dict):
        sid = (data.get("data") or {}).get("id") or data.get("id")
    if not sid:
        raise ConfigError("could not read session id from POST /api/session response")
    r.log.event("session created", sid=str(sid))
    return str(sid)


def prompt(r: Runner, text: str, *, title: str, directory: str) -> str:
    """Create a session and POST /api/session/{id}/prompt through the proxy."""
    with proxy_up(r):
        sid = create_session(r, title=title, directory=directory)
        request(r, "POST", f"/api/session/{sid}/prompt", body={"text": text})
        r.log.event("prompt sent", sid=sid)
        print(f"session: {sid}")
        return sid


def sessions(r: Runner, *, as_json: bool) -> None:
    """GET /api/session and print a compact table (or raw JSON)."""
    with proxy_up(r):
        data = request(r, "GET", "/api/session")
        if r.cfg.dry_run:
            return
        rows = data.get("data") or []
        if as_json:
            print(json.dumps(rows, indent=2))
            return
        for s in rows:
            sid = s.get("id", "")
            title = s.get("title", "")
            directory = (s.get("location") or {}).get("directory", "")
            updated = (s.get("time") or {}).get("updated", "")
            cost = _as_float(s.get("cost"))
            print(f"{sid}  ${cost:.4f}  {directory}  {title}  (updated {updated})")
        if not rows:
            print("(no sessions)")
