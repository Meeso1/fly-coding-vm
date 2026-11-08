"""Keep vibectl's activity log out of the checkout during the test suite."""

import pytest


@pytest.fixture(autouse=True)
def log_root(tmp_path, monkeypatch):
    """Redirect the activity log into a per-test temp dir (also returned)."""
    d = tmp_path / "vibectl-logs"
    monkeypatch.setenv("VIBECTL_LOG_DIR", str(d))
    return d
