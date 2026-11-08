"""Machine-id resolution and parsing."""

import pytest

from vibectl.config import Config, ConfigError
from vibectl.fly import Runner, parse_machine_id

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
