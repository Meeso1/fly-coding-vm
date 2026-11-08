"""vibectl connect-pin helpers and dry-run behaviour."""

from vibectl import cli, pin
from vibectl.config import Config
from vibectl.log import ActivityLog
from vibectl.pin import PIN_PATH, connect_pin, pin_command, pin_expiry, unpin_command


class _PinRunner:
    """Runner stub that records machine_exec calls (no fly/subprocess)."""

    def __init__(self, dry_run):
        self.cfg = Config(
            app="my-vm", machine_id="m123", port=4096, password="x", dry_run=dry_run
        )
        self.calls = []
        self.log = ActivityLog("my-vm", enabled=False)

    def machine_id(self):
        return "m123"

    def machine_exec(self, remote_cmd, *, capture=False, sensitive=False):
        self.calls.append(remote_cmd)
        return ""


def test_pin_expiry_is_now_plus_ttl():
    assert pin_expiry(1_000_000.0) == 1_000_000 + pin.PIN_TTL_SECONDS


def test_pin_and_unpin_commands():
    write = pin_command(12345)
    assert write[0] == "sh" and write[1] == "-c"
    assert "12345" in write[2] and PIN_PATH in write[2]
    assert unpin_command()[2] == f"rm -f {PIN_PATH}"


def test_connect_pin_writes_on_enter_removes_on_exit(monkeypatch):
    # No real refresher thread (would shell out); the initial write + cleanup
    # are still exercised.
    monkeypatch.setattr("vibectl.pin._refresh_loop", lambda r, stop, s: None)
    r = _PinRunner(dry_run=False)
    with connect_pin(r):
        assert len(r.calls) == 1 and PIN_PATH in r.calls[0][2]
    assert r.calls[-1] == unpin_command()
    assert len(r.calls) == 2


def test_connect_pin_dry_run_prints_only(capsys):
    r = _PinRunner(dry_run=True)
    with connect_pin(r):
        pass
    out = capsys.readouterr().out
    assert "refresh the connect pin" in out
    assert len(r.calls) == 2  # write + remove, no real VM writes
    assert r.calls[-1] == unpin_command()


def test_dry_run_connect_shows_pin_commands(monkeypatch, capsys):
    monkeypatch.setenv("FLY_APP", "my-vm")
    monkeypatch.setenv("OPENCODE_PASSWORD", "dummy-secret")
    monkeypatch.delenv("FLY_MACHINE_ID", raising=False)
    monkeypatch.delenv("OC_PORT", raising=False)
    rc = cli.main(["--dry-run", "connect", "--web"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "fly machine exec" in out
    assert PIN_PATH in out  # write + remove equivalents shown
    assert "refresh the connect pin" in out
