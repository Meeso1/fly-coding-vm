"""flyctl wrapper: command runner, machine-id resolution, exec, proxy lifecycle."""

from __future__ import annotations

import contextlib
import json
import shlex
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

from .config import Config, ConfigError, require_fly


def parse_machine_id(payload: str) -> str:
    """Return the machine id from ``fly machine list -a <app> --json`` output."""
    try:
        machines = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"could not parse `fly machine list --json`: {exc}") from exc
    if not isinstance(machines, list) or not machines:
        raise ConfigError(
            "no machines found for FLY_APP; is the env file from `vibectl init` "
            "sourced (e.g. `source <app>.env`) and does `fly machine list -a $FLY_APP "
            "--json` work?"
        )
    # Prefer a live machine over a destroyed one; fall back to the first entry.
    live = [m for m in machines if isinstance(m, dict) and m.get("state") != "destroyed"]
    first = (live or machines)[0]
    mid = first.get("id") if isinstance(first, dict) else None
    if not mid:
        raise ConfigError("could not resolve a machine id from `fly machine list --json`")
    return str(mid)


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    """True if something is already accepting connections on the local port."""
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def wait_for_port(port: int, proc: subprocess.Popen, timeout: float = 15.0) -> bool:
    """Poll until the proxy opens the local port, or the proxy dies / times out."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_open(port):
            return True
        if proc.poll() is not None:
            return False
        time.sleep(0.25)
    return False


# --- local proxy port: OC_PORT override > recorded <app>.port > auto free ---


def port_file_path(app: str) -> Path:
    """Non-secret runtime state: the local port the proxy currently uses."""
    return Path(f"{app}.port")


def read_port_file(app: str) -> int | None:
    """Recorded port, or None when missing/unreadable (stale files are ignored)."""
    try:
        return int(port_file_path(app).read_text().strip())
    except (OSError, ValueError):
        return None


def write_port_file(app: str, port: int) -> None:
    port_file_path(app).write_text(f"{port}\n")


def _free_local_port() -> int:
    """A currently-free TCP port on 127.0.0.1 (tiny race after close; accepted)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def resolve_port(r: Runner) -> int:
    """Resolve the local proxy port and memoize it on ``r.cfg``.

    ``OC_PORT`` overrides everything. Otherwise: reuse the port recorded in
    ``<app>.port`` if it is currently open (a running ``vibectl connect`` —
    enables a second command to piggyback instead of binding a second proxy),
    else pick a free local port and record it there so later ``prompt``/
    ``sessions`` calls (or an `OC_PORT=<port>` export) can pin it. In
    ``--dry-run`` nothing is written.
    """
    if r.cfg.port is not None:
        return r.cfg.port
    recorded = read_port_file(r.cfg.app)
    if recorded is not None and port_open(recorded):
        print(f"note: local proxy port {recorded} already open "
              f"(recorded in {r.cfg.app}.port) — reusing it")
        r.cfg.port = recorded
        return recorded
    port = _free_local_port()
    r.cfg.port = port
    if r.cfg.dry_run:
        print(f"[dry-run] (would record local proxy port {port} in {r.cfg.app}.port)")
    else:
        write_port_file(r.cfg.app, port)
        print(f"note: auto-picked local proxy port {port} — pin it with "
              f"OC_PORT={port} if you want commands to always use it")
    return port


class Runner:
    """Runs fly commands, or just prints them in ``--dry-run`` mode."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def run(self, args: list[str], cwd: str | None = None) -> int:
        """Stream a fly command to the terminal; return its exit code.

        ``cwd`` runs it from another directory (deploy uses a temp copy).
        """
        cmd = ["fly", *args]
        if self.cfg.dry_run:
            print("[dry-run] " + shlex.join(cmd))
            return 0
        require_fly(self.cfg.dry_run)
        return subprocess.run(cmd, cwd=cwd).returncode

    def capture(self, args: list[str], cwd: str | None = None) -> str:
        """Run a fly command capturing stdout (for JSON / password output)."""
        cmd = ["fly", *args]
        if self.cfg.dry_run:
            print("[dry-run] " + shlex.join(cmd))
            return ""
        require_fly(self.cfg.dry_run)
        proc = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
        if proc.returncode != 0:
            raise ConfigError(
                f"fly command failed ({proc.returncode}): {shlex.join(cmd)}\n"
                f"{proc.stderr.strip()}"
            )
        return proc.stdout

    def machine_id(self) -> str:
        """Resolve the machine id (override, else first entry of `machine list`)."""
        if self.cfg.machine_id:
            return self.cfg.machine_id
        out = self.capture(["machine", "list", "-a", self.cfg.app, "--json"])
        if self.cfg.dry_run:
            return "<machine-id>"
        return parse_machine_id(out)

    def machine_exec(self, remote_cmd: list[str], *, capture: bool = False) -> int | str:
        """Run a remote command via ``fly machine exec``.

        flyctl expects ONE string after ``--`` which it word-splits and runs
        WITHOUT a shell. We join argv with ``shlex.quote`` per token so it splits
        back into the same argv. For pipes/compound commands pass
        ``sh -c '...'`` as the remote command.
        """
        mid = self.machine_id()
        cmd_str = " ".join(shlex.quote(a) for a in remote_cmd)
        args = ["machine", "exec", mid, "-a", self.cfg.app, "--", cmd_str]
        if capture:
            return self.capture(args).strip()
        return self.run(args)


@contextlib.contextmanager
def proxy_up(r: Runner) -> Iterator[None]:
    """Yield with the local OC port forwarded to the VM's :4096.

    The port comes from
    :func:`resolve_port` (``OC_PORT`` override / `<app>.port` / free port). If
    an already-open port is reused (e.g. a running ``vibectl connect``) no new
    proxy is started. Otherwise ``fly proxy`` is spawned as a CHILD of this
    process, waits for the local port, and terminates it on exit. In
    ``--dry-run`` it only prints the commands.
    """
    port = resolve_port(r)
    if not r.cfg.dry_run and port_open(port):
        yield
        return

    cmd = ["fly", "proxy", f"{port}:4096", "-a", r.cfg.app]
    if r.cfg.dry_run:
        print("[dry-run] " + shlex.join(cmd))
        print(f"[dry-run] (wait for 127.0.0.1:{port}, run the client, kill proxy on exit)")
        yield
        return
    if r.cfg.dry_run:
        print("[dry-run] " + shlex.join(cmd))
        print(f"[dry-run] (wait for 127.0.0.1:{port}, run the client, kill proxy on exit)")
        yield
        return

    require_fly(False)
    proc = subprocess.Popen(cmd)
    try:
        if not wait_for_port(port, proc):
            raise ConfigError(
                f"fly proxy did not open 127.0.0.1:{port} in time; "
                "check `fly doctor` / WireGuard."
            )
        yield
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
