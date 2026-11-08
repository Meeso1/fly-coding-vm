"""Unit tests for the idle watcher: connect pins and per-run LLM cost metrics."""

import contextlib
import json
import os
import types

import pytest

from vm_tools import watch


class _LoopBreak(Exception):
    """Raised from a fake time.sleep to break run()'s infinite loop."""


class _Clock:
    def __init__(self, start, stop_after=None):
        self.t = float(start)
        self.sleeps = 0
        self.stop_after = stop_after

    def time(self):
        return self.t

    def strftime(self, fmt):
        return "2026-01-01T00:00:00+0000"

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s
        self.sleeps += 1
        if self.stop_after is not None and self.sleeps >= self.stop_after:
            raise _LoopBreak


def _patch_time(monkeypatch, start=1_000_000, stop_after=None):
    clock = _Clock(start, stop_after)
    mod = types.SimpleNamespace(
        time=clock.time, sleep=clock.sleep, strftime=clock.strftime, monotonic=clock.monotonic
    )
    monkeypatch.setattr(watch, "time", mod)
    return clock


def _pin(state_dir, text, mtime=None):
    p = state_dir / "connect-pin"
    p.write_text(text)
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


# --- pin_valid -------------------------------------------------------------


def test_pin_valid_missing(tmp_path):
    assert watch.pin_valid(tmp_path, now=1000) is False


def test_pin_valid_unparseable(tmp_path):
    _pin(tmp_path, "garbage")
    assert watch.pin_valid(tmp_path, now=1000) is False


def test_pin_valid_expired(tmp_path):
    _pin(tmp_path, "900")  # <= now
    assert watch.pin_valid(tmp_path, now=1000) is False


def test_pin_valid_fresh(tmp_path):
    _pin(tmp_path, "2000")  # > now
    assert watch.pin_valid(tmp_path, now=1000) is True


def test_pin_valid_ignores_previous_boot(tmp_path):
    # Unexpired, but the file predates this boot's started_at -> ignore.
    _pin(tmp_path, "2000", mtime=900)
    assert watch.pin_valid(tmp_path, now=1200, started=1000) is False


def test_pin_valid_current_boot(tmp_path):
    _pin(tmp_path, "2000", mtime=1100)
    assert watch.pin_valid(tmp_path, now=1200, started=1000) is True


# --- compute_busy ----------------------------------------------------------


def test_compute_busy_pinned():
    assert watch.compute_busy(0, 0, 1e9, 15, True) is True


def test_compute_busy_running():
    assert watch.compute_busy(1, 0, 1e9, 15, False) is True


def test_compute_busy_recently_updated():
    now = 1e9
    assert watch.compute_busy(0, now * 1000 - 1000, now, 15, False) is True


def test_compute_busy_idle():
    assert watch.compute_busy(0, 0, 1e9, 15, False) is False


# --- session_costs / append_metrics ----------------------------------------


def test_session_costs_sums_and_breakdown(monkeypatch):
    monkeypatch.setattr(
        watch,
        "all_sessions",
        lambda c: [
            {"id": "a", "title": "A", "cost": 1.0, "tokens": {"input": 5}},
            {"id": "b", "title": "B", "cost": 2.5},
            {"id": "c"},  # missing cost -> 0
        ],
    )
    total, rows = watch.session_costs(object())
    assert total == 3.5
    assert rows[0] == {"id": "a", "title": "A", "cost": 1.0, "tokens": {"input": 5}}
    assert rows[2]["cost"] == 0.0
    assert rows[2]["tokens"] is None


def test_append_metrics_writes_one_json_line(tmp_path):
    watch.append_metrics(
        tmp_path, started=100, reason="idle", llm_cost_usd=3.5, sessions=[{"id": "a", "cost": 3.5}]
    )
    lines = (tmp_path / "metrics.jsonl").read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["started_at"] == 100
    assert "stopped_at" in rec
    assert rec["reason"] == "idle"
    assert rec["llm_cost_usd"] == 3.5
    assert rec["sessions"][0]["id"] == "a"


def test_append_metrics_appends(tmp_path):
    watch.append_metrics(tmp_path, started=1, reason="idle", llm_cost_usd=1, sessions=[])
    watch.append_metrics(tmp_path, started=2, reason="max_uptime", llm_cost_usd=2, sessions=[])
    assert len((tmp_path / "metrics.jsonl").read_text().splitlines()) == 2


def test_record_run_metrics_collects_and_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(watch, "oc_client", lambda p, pw: contextlib.nullcontext(object()))
    monkeypatch.setattr(
        watch, "all_sessions", lambda c: [{"id": "s1", "title": "T", "cost": 2.0}]
    )
    watch.record_run_metrics(4096, "pw", 100, "idle", tmp_path)
    rec = json.loads((tmp_path / "metrics.jsonl").read_text().strip())
    assert rec["reason"] == "idle"
    assert rec["llm_cost_usd"] == 2.0
    assert rec["sessions"][0]["id"] == "s1"


def test_record_run_metrics_survives_api_error(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("api down")

    monkeypatch.setattr(watch, "oc_client", boom)
    watch.record_run_metrics(4096, "pw", 100, "max_uptime", tmp_path)
    rec = json.loads((tmp_path / "metrics.jsonl").read_text().strip())
    assert rec["reason"] == "max_uptime"
    assert rec["llm_cost_usd"] == 0.0
    assert rec["sessions"] == []


# --- run() integration -----------------------------------------------------


def _patch_api(monkeypatch, sessions):
    monkeypatch.setattr(watch, "stop_machine", lambda: None)
    monkeypatch.setattr(watch, "oc_client", lambda p, pw: contextlib.nullcontext(object()))
    monkeypatch.setattr(watch, "active_count", lambda c: 0)
    monkeypatch.setattr(watch, "all_sessions", lambda c: sessions)


def test_run_idle_stops_and_records_metrics(tmp_path, monkeypatch):
    _patch_time(monkeypatch)
    _patch_api(monkeypatch, [{"id": "s", "title": "T", "cost": 1.5}])
    rc = watch.run(
        idle_minutes=0, max_uptime_hours=12, port=4096, poll_seconds=1,
        state_dir=tmp_path, password="pw",
    )
    assert rc == 0
    rec = json.loads((tmp_path / "metrics.jsonl").read_text().strip())
    assert rec["reason"] == "idle"
    assert rec["llm_cost_usd"] == 1.5


def test_run_max_uptime_records_reason(tmp_path, monkeypatch):
    _patch_time(monkeypatch)
    _patch_api(monkeypatch, [])
    rc = watch.run(
        idle_minutes=15, max_uptime_hours=0, port=4096, poll_seconds=1,
        state_dir=tmp_path, password="pw",
    )
    assert rc == 0
    rec = json.loads((tmp_path / "metrics.jsonl").read_text().strip())
    assert rec["reason"] == "max_uptime"


def test_run_valid_pin_prevents_idle_stop(tmp_path, monkeypatch):
    _pin(tmp_path, "9999999999")  # far-future, unexpired pin
    _patch_time(monkeypatch, stop_after=3)
    stops = {"n": 0}
    monkeypatch.setattr(watch, "stop_machine", lambda: stops.__setitem__("n", stops["n"] + 1))
    monkeypatch.setattr(watch, "oc_client", lambda p, pw: contextlib.nullcontext(object()))
    monkeypatch.setattr(watch, "active_count", lambda c: 0)
    monkeypatch.setattr(watch, "all_sessions", lambda c: [])
    with pytest.raises(_LoopBreak):
        watch.run(
            idle_minutes=0, max_uptime_hours=12, port=4096, poll_seconds=1,
            state_dir=tmp_path, password="pw",
        )
    assert stops["n"] == 0  # never idle-stopped while connected
    assert not (tmp_path / "metrics.jsonl").exists()  # no stop -> no metrics line
