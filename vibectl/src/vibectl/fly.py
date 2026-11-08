"""flyctl wrapper: command runner, machine-id resolution, exec, proxy lifecycle."""

from __future__ import annotations

import contextlib
import json
import shlex
import socket
import subprocess
import time
from collections.abc import Iterator

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


class Runner:
    """Runs fly commands, or just prints them in ``--dry-run`` mode."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def run(self, args: list[str]) -> int:
        """Stream a fly command to the terminal; return its exit code."""
        cmd = ["fly", *args]
        if self.cfg.dry_run:
            print("[dry-run] " + shlex.join(cmd))
            return 0
        require_fly(self.cfg.dry_run)
        return subprocess.run(cmd).returncode

    def capture(self, args: list[str]) -> str:
        """Run a fly command capturing stdout (for JSON / password output)."""
        cmd = ["fly", *args]
        if self.cfg.dry_run:
            print("[dry-run] " + shlex.join(cmd))
            return ""
        require_fly(self.cfg.dry_run)
        proc = subprocess.run(cmd, text=True, capture_output=True)
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

    Reuses an already-open port (e.g. a running ``vibectl connect``) so a second
    proxy does not collide. Otherwise starts ``fly proxy`` as a CHILD of this
    process, waits for the local port, and terminates it on exit. In
    ``--dry-run`` it only prints the command.
    """
    port = r.cfg.port
    if not r.cfg.dry_run and port_open(port):
        yield
        return

    cmd = ["fly", "proxy", f"{port}:4096", "-a", r.cfg.app]
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
