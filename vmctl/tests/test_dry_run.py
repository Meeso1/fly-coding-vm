"""--dry-run renders fly/curl commands without needing flyctl or a live app."""

from vmctl import cli


def _env(monkeypatch):
    monkeypatch.setenv("FLY_APP", "fly-coding-vm")
    monkeypatch.setenv("OPENCODE_PASSWORD", "dummy-secret")
    monkeypatch.delenv("FLY_MACHINE_ID", raising=False)
    monkeypatch.delenv("OC_PORT", raising=False)


def test_dry_run_start(monkeypatch, capsys):
    _env(monkeypatch)
    rc = cli.main(["--dry-run", "start"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "fly machine list -a fly-coding-vm --json" in out
    assert "fly machine start" in out
    assert "fly machine wait" in out and "--state started" in out


def test_dry_run_prompt_prints_curl(monkeypatch, capsys):
    _env(monkeypatch)
    rc = cli.main(["--dry-run", "prompt", "hello"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "curl" in out
    assert "/api/session" in out
    assert "/api/session/<session-id>/prompt" in out
    assert "$OPENCODE_PASSWORD" in out
    assert "dummy-secret" not in out


def test_dry_run_exec_single_string(monkeypatch, capsys):
    _env(monkeypatch)
    rc = cli.main(["--dry-run", "exec", "--", "sh", "-c", "echo hi | grep hi"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "fly machine exec" in out
    assert "sh -c" in out
