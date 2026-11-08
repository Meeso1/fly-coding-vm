"""`vibectl init`: dry-run sequence, env-file writing, fly.toml region patch."""

import argparse
from argparse import Namespace

import pytest

from vibectl import provision
from vibectl.config import Config, ConfigError
from vibectl.fly import Runner

TOML = (
    'app = "coding-vm"\n'
    'primary_region = "iad"\n'
    "\n"
    "[build]\n"
    '  dockerfile = "Dockerfile"\n'
)


class FakeRunner(Runner):
    """Records fly commands instead of running them."""

    def __init__(self, app: str = "my-vm", dry_run: bool = True) -> None:
        super().__init__(
            Config(app=app, machine_id=None, port=4096, password=None, dry_run=dry_run)
        )
        self.calls: list[list[str]] = []

    def run(self, args: list[str]) -> int:
        print("[dry-run] " + " ".join(args))
        self.calls.append(args)
        return 0


def make_args(**kw) -> Namespace:
    base: dict = dict(
        app="my-vm",
        region=None,
        volume_size=5,
        config="fly.toml",
        env_file=None,
        password=None,
        github_app_id=None,
        pem=None,
        openrouter_key=None,
        force=False,
        deploy=False,
    )
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Empty project dir with a minimal fly.toml; fly 'exists' on PATH."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "fly.toml").write_text(TOML)
    monkeypatch.setattr("vibectl.config.shutil.which", lambda *_a, **_k: "/usr/bin/fly")
    for var in ("GITHUB_APP_ID", "GITHUB_APP_PRIVATE_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


# --- pure helpers ---------------------------------------------------------


def test_read_top_level_ignores_sections(repo):
    top = provision.read_top_level(repo / "fly.toml")
    assert top == {"app": "coding-vm", "primary_region": "iad"}


def test_patch_primary_region_keeps_sections():
    out = provision.patch_primary_region(TOML, "fra")
    assert 'primary_region = "fra"' in out
    assert 'dockerfile = "Dockerfile"' in out


# --- dry-run behaviour ----------------------------------------------------


def test_dry_run_prints_all_steps(repo, capsys):
    r = FakeRunner()
    assert provision.init(r, make_args()) == 0
    out = capsys.readouterr().out
    assert "apps create my-vm" in out
    assert "volumes create data --region iad --size 5" in out
    assert "secrets set -a my-vm" in out
    assert "$OPENCODE_PASSWORD" in out  # placeholders, never real values


def test_dry_run_no_deploy_no_env_file(repo, capsys):
    r = FakeRunner()
    provision.init(r, make_args())
    out = capsys.readouterr().out
    assert "fly deploy" not in out
    assert "would write" in out
    assert not (repo / "my-vm.env").exists()


def test_dry_run_deploy_flag(repo):
    r = FakeRunner()
    provision.init(r, make_args(deploy=True))
    assert r.calls[-1][:2] == ["deploy", "-a"]


# --- real behaviour (FakeRunner records the fly commands) ------------------


def test_steps_sequence_and_secrets(repo, capsys):
    (repo / "app.pem").write_text("-----BEGIN PRIVATE KEY-----\nabc\n-----END-----\n")
    r = FakeRunner(dry_run=False)
    assert provision.init(
        r,
        make_args(
            password="secret123",
            github_app_id="123",
            pem=str(repo / "app.pem"),
            openrouter_key="sk-or-x",
        ),
    ) == 0

    assert r.calls[0] == ["apps", "create", "my-vm"]
    assert r.calls[1][:3] == ["volumes", "create", "data"]
    assert "--region" in r.calls[1] and "--size" in r.calls[1]

    secrets_cmd = next(c for c in r.calls if c and c[0] == "secrets")
    joined = " ".join(secrets_cmd[4:])
    assert "OPENCODE_PASSWORD=secret123" in joined
    assert "GITHUB_APP_ID=123" in joined
    assert "GITHUB_APP_PRIVATE_KEY=-----BEGIN PRIVATE KEY-----" in joined
    assert "OPENROUTER_API_KEY=sk-or-x" in joined


def test_dry_run_credential_placeholders_never_leak(repo, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_APP_ID", "real-app-id")
    monkeypatch.setenv("OPENROUTER_API_KEY", "real-key")
    r = FakeRunner()
    provision.init(r, make_args())
    out = capsys.readouterr().out
    assert "real-app-id" not in out and "real-key" not in out  # placeholders only


def test_missing_credentials_skipped_with_note(repo, capsys):
    r = FakeRunner(dry_run=False)
    assert provision.init(r, make_args(password="secret123")) == 0
    out = capsys.readouterr().out
    assert "skipping GITHUB_APP_ID" in out
    assert "skipping GITHUB_APP_PRIVATE_KEY" in out
    assert "skipping OPENROUTER_API_KEY" in out


def test_writes_env_file(repo, capsys):
    r = FakeRunner(dry_run=False)
    assert provision.init(r, make_args(password="secret123", env_file="cfg.env")) == 0
    text = (repo / "cfg.env").read_text()
    assert "export FLY_APP=my-vm" in text
    assert "export OPENCODE_PASSWORD=secret123" in text
    assert (repo / "cfg.env").stat().st_mode & 0o777 == 0o600
    out = capsys.readouterr().out
    assert "source cfg.env" in out
    assert "vibectl deploy" in out


def test_generated_password_length(repo, capsys):
    r = FakeRunner(dry_run=False)
    provision.init(r, make_args())  # no --password -> generated
    text = (repo / "my-vm.env").read_text()
    assert "export OPENCODE_PASSWORD=" + ("a" * 48) not in text  # e.g. a bit
    pw = [
        line
        for line in text.splitlines()
        if line.startswith("export OPENCODE_PASSWORD=")
    ][0]
    assert len(pw.split("=", 1)[1].strip("'")) == 48


def test_env_file_refuses_overwrite_without_force(repo):
    (repo / "my-vm.env").write_text("export FLY_APP=keep\n")
    r = FakeRunner(dry_run=False)
    with pytest.raises(ConfigError, match="--force"):
        provision.init(r, make_args(password="secret123"))
    assert (repo / "my-vm.env").read_text().strip() == "export FLY_APP=keep"


def test_force_overwrites_env_file(repo):
    (repo / "my-vm.env").write_text("export FLY_APP=old\n")
    r = FakeRunner(dry_run=False)
    provision.init(r, make_args(password="secret123", force=True))
    assert "export FLY_APP=my-vm" in (repo / "my-vm.env").read_text()


# --- fly.toml region patch -------------------------------------------------


def test_region_flag_patches_fly_toml(repo, capsys):
    r = FakeRunner(dry_run=False)
    assert provision.init(r, make_args(password="secret123", region="fra")) == 0
    text = (repo / "fly.toml").read_text()
    assert 'primary_region = "fra"' in text
    assert 'dockerfile = "Dockerfile"' in text
    assert 'updated fly.toml: primary_region = "fra"' in capsys.readouterr().out


def test_explicit_same_region_skips_patch(repo, capsys):
    r = FakeRunner(dry_run=False)
    provision.init(r, make_args(password="secret123", region="iad"))
    assert "updated fly.toml" not in capsys.readouterr().out


def test_region_defaults_to_fly_toml(repo, capsys):
    r = FakeRunner(dry_run=True)
    provision.init(r, make_args())
    assert "--region iad" in capsys.readouterr().out


def test_missing_fly_toml_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = FakeRunner()
    with pytest.raises(ConfigError, match="cannot read"):
        provision.init(r, make_args())


def test_failing_step_aborts(repo, monkeypatch):
    r = FakeRunner(dry_run=False)

    def failing_run(args: list[str]) -> int:
        r.calls.append(args)
        return 1

    monkeypatch.setattr(r, "run", failing_run)
    with pytest.raises(ConfigError, match="apps create"):
        provision.init(r, make_args(password="secret123"))
    assert len(r.calls) == 1  # fails fast, no later steps
