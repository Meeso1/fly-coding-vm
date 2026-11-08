"""Machine-id resolution, parsing, and local port resolution."""

import socket

import pytest

from vibectl.config import Config, ConfigError
from vibectl.fly import Runner, parse_machine_id, read_port_file, resolve_port

SAMPLE = (
    '[{"id":"3287e6a","name":"my-vm","state":"stopped"},'
    '{"id":"aaa111","name":"my-vm","state":"started"}]'
)


def test_parse_machine_id():
    assert parse_machine_id(SAMPLE) == "3287e6a"


def test_parse_machine_id_skips_destroyed():
    payload = '[{"id":"dead","state":"destroyed"},{"id":"live","state":"stopped"}]'
    assert parse_machine_id(payload) == "live"


def test_parse_machine_id_empty_raises():
    with pytest.raises(ConfigError):
        parse_machine_id("[]")


def test_machine_id_override_skips_fly(monkeypatch):
    cfg = Config(
        app="app", machine_id="override-123", port=4096, password=None, dry_run=False
    )
    r = Runner(cfg)

    def boom(*_args, **_kwargs):
        raise AssertionError("fly must not be called when override is set")

    monkeypatch.setattr(r, "capture", boom)
    assert r.machine_id() == "override-123"


# --- local proxy port resolution --------------------------------------------


def _cfg(port, dry_run=False):
    return Config(app="my-vm", machine_id=None, port=port, password=None, dry_run=dry_run)


def test_port_override_wins_no_file_written(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OC_PORT", "5000")
    r = Runner(_cfg(5000))
    assert resolve_port(r) == 5000
    assert not (tmp_path / "my-vm.port").exists()  # override needs no state file


def test_port_unset_picks_free_and_records(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OC_PORT", raising=False)
    r = Runner(_cfg(None))
    port = resolve_port(r)
    assert isinstance(port, int) and 1024 <= port <= 65535
    assert (tmp_path / "my-vm.port").read_text().strip() == str(port)
    assert r.cfg.port == port  # memoized on the config


def test_port_unset_reuses_recorded_open_port(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OC_PORT", raising=False)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        open_port = s.getsockname()[1]
        (tmp_path / "my-vm.port").write_text(f"{open_port}\n")
        r = Runner(_cfg(None))
        assert resolve_port(r) == open_port  # proxy piggybacks on it
        assert (tmp_path / "my-vm.port").read_text().strip() == str(open_port)


def test_port_recorded_but_not_open_picks_new(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OC_PORT", raising=False)
    (tmp_path / "my-vm.port").write_text("59999\n")  # nothing listens there
    r = Runner(_cfg(None))
    assert resolve_port(r) != 59999
    assert (tmp_path / "my-vm.port").read_text().strip() == str(r.cfg.port)


def test_read_port_file_missing_or_broken(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert read_port_file("my-vm") is None
    (tmp_path / "my-vm.port").write_text("not-a-port\n")
    assert read_port_file("my-vm") is None


def test_dry_run_port_resolution_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OC_PORT", raising=False)
    r = Runner(_cfg(None, dry_run=True))
    port = resolve_port(r)
    assert port > 0
    assert not (tmp_path / "my-vm.port").exists()  # dry-run writes no files
    assert "would record" in capsys.readouterr().out
