"""vibectl writes a redacted, additive activity log (stdout/stderr unchanged)."""

from vibectl import cli
from vibectl.log import ActivityLog


def _env(monkeypatch):
    monkeypatch.setenv("FLY_APP", "my-vm")
    monkeypatch.setenv("OPENCODE_PASSWORD", "dummy-secret")
    monkeypatch.delenv("FLY_MACHINE_ID", raising=False)
    monkeypatch.delenv("OC_PORT", raising=False)


def _log_text(log_root, app="my-vm"):
    return (log_root / f"vibectl-{app}.log").read_text()


def test_dry_run_writes_marked_log(log_root, monkeypatch, capsys):
    _env(monkeypatch)
    rc = cli.main(["--dry-run", "status"])
    assert rc == 0
    text = _log_text(log_root)
    assert "[DRY]" in text
    assert "[RUN]" not in text
    assert "invoke" in text
    assert "fly machine list" in text  # fly command captured
    assert "exit" in text
    # stdout is the normal dry-run output, untouched by logging
    assert "[dry-run]" in capsys.readouterr().out


def test_real_mode_logs_run_tag_and_exit_code(log_root, monkeypatch):
    _env(monkeypatch)
    monkeypatch.setenv("FLY_MACHINE_ID", "m123")  # no `fly machine list` call
    monkeypatch.setattr("vibectl.config.shutil.which", lambda *_a, **_k: "/usr/bin/fly")

    class Result:
        returncode = 0

    monkeypatch.setattr("vibectl.fly.subprocess.run", lambda *a, **k: Result())
    rc = cli.main(["start"])
    assert rc == 0
    text = _log_text(log_root)
    assert "[RUN]" in text
    assert "fly machine start m123 -a my-vm -> exit 0" in text
    assert "machine start" in text  # important event
    assert "exit" in text


def test_error_is_logged(log_root, monkeypatch, capsys):
    monkeypatch.delenv("FLY_APP", raising=False)
    monkeypatch.delenv("FLY_MACHINE_ID", raising=False)
    rc = cli.main(["--dry-run", "status"])
    assert rc == 2
    text = _log_text(log_root, app="unknown")
    assert "error" in text
    assert "FLY_APP is not set" in text


def test_log_file_is_private(log_root, monkeypatch):
    _env(monkeypatch)
    cli.main(["--dry-run", "status"])
    assert (log_root / "vibectl-my-vm.log").stat().st_mode & 0o777 == 0o600


def test_redact_secrets(tmp_path):
    log = ActivityLog("my-vm", logs_dir=tmp_path)
    log.add_secret("topsecretpassword")
    pem = "-----BEGIN PRIVATE KEY-----\nSECRETBASE64\n-----END PRIVATE KEY-----"
    text = (
        "fly secrets set "
        "'OPENCODE_PASSWORD=topsecretpassword' "
        f"'GITHUB_APP_PRIVATE_KEY={pem}' "
        "token ghs_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
    )
    out = log.redact(text)
    assert "topsecretpassword" not in out
    assert "SECRETBASE64" not in out  # whole multi-line PEM token is consumed
    assert "ghs_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345" not in out
    assert out.count("<redacted>") >= 3
    # key names are preserved for readability
    assert "OPENCODE_PASSWORD=<redacted>" in out
    assert "GITHUB_APP_PRIVATE_KEY=<redacted>" in out


def test_disabled_log_is_noop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    log = ActivityLog("my-vm", enabled=False)
    log.write("fly", "something")
    log.event("boom", x=1)
    assert not (tmp_path / "logs").exists()
