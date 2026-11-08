"""vibectl metrics (per-run LLM cost) and the sessions cost column."""

import contextlib
import json

from vibectl import api, cli
from vibectl.config import Config
from vibectl.fly import Runner
from vibectl.metrics import METRICS_PATH, show_metrics


def _cfg(dry_run=False):
    return Config(
        app="my-vm", machine_id="m", port=4096, password="x", dry_run=dry_run
    )


class _ExecRunner:
    """Minimal stub whose machine_exec returns a canned metrics.jsonl."""

    def __init__(self, raw, dry_run=False):
        self.raw = raw
        self.cfg = _cfg(dry_run)
        self.calls = []

    def machine_exec(self, remote_cmd, *, capture=False, sensitive=False):
        self.calls.append(remote_cmd)
        return self.raw


def _record(reason="idle", cost=1.25, sessions=None):
    return json.dumps(
        {
            "started_at": 1_700_000_000,
            "stopped_at": 1_700_003_600,
            "reason": reason,
            "llm_cost_usd": cost,
            "sessions": sessions
            if sessions is not None
            else [{"id": "ses_1", "title": "repo work", "cost": cost, "tokens": None}],
        }
    )


def test_show_metrics_prints_runs(capsys):
    raw = _record() + "\n" + _record(reason="max_uptime", cost=0.5) + "\n"
    show_metrics(_ExecRunner(raw))
    out = capsys.readouterr().out
    assert "run 2023-11-14" in out  # UTC-formatted start/stop
    assert "reason=idle" in out
    assert "reason=max_uptime" in out
    assert "llm_cost_usd=$1.2500" in out
    assert "ses_1" in out and "repo work" in out


def test_show_metrics_reads_metrics_path(capsys):
    r = _ExecRunner(_record())
    show_metrics(r)
    assert r.calls == [["cat", METRICS_PATH]]


def test_show_metrics_no_records(capsys):
    show_metrics(_ExecRunner(""))
    assert "no VM runs recorded yet" in capsys.readouterr().out


def test_show_metrics_skips_bad_lines(capsys):
    show_metrics(_ExecRunner("not-json\n" + _record() + "\n"))
    out = capsys.readouterr().out
    assert "reason=idle" in out  # good line still printed
    assert "not-json" not in out


def test_dry_run_metrics_prints_fly_equivalent(monkeypatch, capsys):
    monkeypatch.setenv("FLY_APP", "my-vm")
    monkeypatch.delenv("OPENCODE_PASSWORD", raising=False)
    monkeypatch.delenv("FLY_MACHINE_ID", raising=False)
    monkeypatch.delenv("OC_PORT", raising=False)
    rc = cli.main(["--dry-run", "metrics"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "fly machine exec" in out
    assert METRICS_PATH in out


def test_sessions_shows_cost_column(monkeypatch, capsys):
    # Bypass the proxy and feed a canned /api/session response.
    monkeypatch.setattr(api, "proxy_up", lambda r: contextlib.nullcontext())
    monkeypatch.setattr(
        api,
        "request",
        lambda r, m, p, **k: {
            "data": [
                {
                    "id": "ses_1",
                    "title": "work",
                    "location": {"directory": "/root/work/repo"},
                    "time": {"updated": 123},
                    "cost": 0.42,
                }
            ]
        },
    )
    api.sessions(Runner(_cfg()), as_json=False)
    out = capsys.readouterr().out
    assert "$0.4200" in out
    assert "work" in out
