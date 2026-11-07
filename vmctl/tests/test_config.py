"""Config comes ONLY from env, never from a .env file."""

from vmctl import cli


def test_missing_fly_app_errors_and_mentions_sourcing(tmp_path, monkeypatch, capsys):
    (tmp_path / ".env").write_text("export FLY_APP=should_not_be_read\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FLY_APP", raising=False)
    monkeypatch.delenv("FLY_MACHINE_ID", raising=False)

    rc = cli.main(["--dry-run", "status"])
    captured = capsys.readouterr()

    assert rc != 0
    assert "source .env" in captured.err
    assert "should_not_be_read" not in captured.err
    assert "should_not_be_read" not in captured.out


def test_env_wins_and_dotenv_is_ignored(tmp_path, monkeypatch, capsys):
    (tmp_path / ".env").write_text("export FLY_APP=from_dotenv\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FLY_APP", "from_env")
    monkeypatch.setenv("OPENCODE_PASSWORD", "dummy-secret")
    monkeypatch.delenv("FLY_MACHINE_ID", raising=False)
    monkeypatch.delenv("OC_PORT", raising=False)

    rc = cli.main(["--dry-run", "status"])
    captured = capsys.readouterr()

    assert rc == 0
    assert "from_env" in captured.out
    assert "from_dotenv" not in captured.out
    assert "dummy-secret" not in captured.out
