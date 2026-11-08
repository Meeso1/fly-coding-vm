"""Keep the VM from being idle-stopped while ``vibectl connect`` is running.

On connect a freshness pin is written to the VM's /data volume via
``fly machine exec`` (expiry = now + 90s). A background thread refreshes it
every 30s and the pin is removed on exit. vm-tools watch.py treats a live pin
as "busy", so the idle timer cannot fire while a client is connected.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Iterator

from .fly import Runner

PIN_PATH = "/data/state/connect-pin"
PIN_TTL_SECONDS = 90
PIN_REFRESH_SECONDS = 30


def pin_expiry(now: float | None = None) -> int:
    """The expiry epoch to write for a pin created ``now``."""
    return int(now if now is not None else time.time()) + PIN_TTL_SECONDS


def pin_command(expiry: int) -> list[str]:
    """Remote argv that writes ``expiry`` to the pin file (no shell needed)."""
    return ["sh", "-c", f"printf '%s' {expiry} > {PIN_PATH}"]


def unpin_command() -> list[str]:
    """Remote argv that removes the pin file."""
    return ["sh", "-c", f"rm -f {PIN_PATH}"]


def _write_pin(r: Runner, expiry: int) -> None:
    r.machine_exec(pin_command(expiry), capture=True)


def _remove_pin(r: Runner) -> None:
    r.machine_exec(unpin_command(), capture=True)


def _refresh_loop(r: Runner, stop: threading.Event, refresh_seconds: int) -> None:
    while not stop.is_set():
        if stop.wait(refresh_seconds):
            return
        try:
            _write_pin(r, pin_expiry())
        except Exception as exc:  # best-effort: never kill the connect session
            r.log.event("connect pin refresh error", detail=str(exc))


@contextlib.contextmanager
def connect_pin(
    r: Runner, *, refresh_seconds: int = PIN_REFRESH_SECONDS
) -> Iterator[None]:
    """Hold a freshness pin on the VM for the duration of the connection."""
    if r.cfg.dry_run:
        # print only: show the fly equivalents, start no thread, write nothing
        _write_pin(r, pin_expiry())
        print(f"[dry-run] (refresh the connect pin every {refresh_seconds}s while connected)")
        try:
            yield
        finally:
            _remove_pin(r)
        return

    # Resolve (and cache) the machine id once so the refresher thread does not
    # re-run `fly machine list` every 30s.
    r.cfg.machine_id = r.machine_id()
    r.log.event("connect pin start", path=PIN_PATH, ttl=PIN_TTL_SECONDS)
    stop = threading.Event()
    thread: threading.Thread | None = None
    try:
        try:
            _write_pin(r, pin_expiry())
        except Exception as exc:
            r.log.event("connect pin write error", detail=str(exc))
        thread = threading.Thread(
            target=_refresh_loop, args=(r, stop, refresh_seconds), daemon=True
        )
        thread.start()
        yield
    finally:
        stop.set()
        if thread is not None:
            thread.join(timeout=5)
        try:
            _remove_pin(r)
        except Exception as exc:
            r.log.event("connect pin removal error", detail=str(exc))
        r.log.event("connect pin stop", path=PIN_PATH)
