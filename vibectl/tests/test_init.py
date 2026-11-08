"""`vibectl init`: dry-run sequence, env-file lifecycle, temp-copy deploy."""

import argparse
import shutil
from argparse import Namespace
from pathlib import Path

import pytest

from vibectl import provision
from vibectl.config import Config, ConfigError
from vibectl.fly import Runner
from vibectl.provision import deploy_from

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
        self.cwds: list[str | None] = []

    def run(self, args: list[str], cwd: str | None = None) -> int:
        print("[dry-run] " + " ".join(args))
        self.calls.append(args)
        self.cwds.append(cwd)
        return 0


def make_args(**kw) -> Namespace:
    base: dict = dict(
        app="my-vm",
        region=None,
        volume_size=5,
        env_file=None,
        password=None,
        github_app_id=None,
        pem=None,
        openrouter_key=None,
        deploy=False,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def make_deploy_files(d: Path) -> None:
    """Complete build context next to fly.toml (what deploy copies)."""
    (d / "Dockerfile").write_text("FROM debian:bookworm-slim\n")
    (d / "entrypoint.sh").write_text("#!/usr/bin/env bash\n")
    for name in ("vm-tools", "skills", "home"):
        (d / name).mkdir()
        (d / name / "f").write_text("x\n")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Empty dir with a minimal fly.toml; fly 'exists' on PATH; no credentials."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "fly.toml").write_text(TOML)
    monkeypatch.setattr("vibectl.config.shutil.which", lambda *_a, **_k: "/usr/bin/fly")
    for var in (
        "GITHUB_APP_ID",
        "GITHUB_APP_PRIVATE_KEY",
        "OPENROUTER_API_KEY",
        "FLY_REGION",
    ):
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


def test_dry_run_deploy_flag(repo, capsys):
    r = FakeRunner()
    provision.init(r, make_args(deploy=True))
    out = capsys.readouterr().out
    assert r.calls[-1][:2] == ["deploy", "-a"]
    assert "temp dir" in out  # deploy runs from a throwaway copy


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


# --- env-file lifecycle: reserve (commented) -> finalize -> discard --------


def test_env_file_reserved_all_commented_then_removed_on_failure(repo):
    r = FakeRunner(dry_run=False)
    snapshots: list[str] = []

    def failing_run(args: list[str], cwd: str | None = None) -> int:
        if args[:2] == ["apps", "create"]:  # first fly step, right after reserve
            snapshots.append((repo / "my-vm.env").read_text())
            return 1
        return 0

    r.run = failing_run
    with pytest.raises(ConfigError, match="apps create"):
        provision.init(r, make_args(password="secret123"))

    provisional = snapshots[0]
    # every nonblank line is commented: sourcing must export NOTHING
    assert all(
        line.lstrip().startswith("#") or not line.strip()
        for line in provisional.splitlines()
    )
    assert "secret123" not in provisional  # real password never provisional
    assert "FLY_REGION='iad'" in provisional or "FLY_REGION=iad" in provisional
    assert not (repo / "my-vm.env").exists()  # deleted -> retry is unblocked


def test_finalized_env_file(repo, capsys):
    r = FakeRunner(dry_run=False)
    assert provision.init(r, make_args(password="secret123", env_file="cfg.env")) == 0
    text = (repo / "cfg.env").read_text()
    assert "export FLY_APP=my-vm" in text
    assert "export FLY_REGION='iad'" in text or "export FLY_REGION=iad" in text
    assert "export OPENCODE_PASSWORD=secret123" in text
    assert "do NOT source yet" not in text  # finalized, not the provisional stub
    assert (repo / "cfg.env").stat().st_mode & 0o777 == 0o600
    out = capsys.readouterr().out
    assert "source cfg.env" in out
    assert "vibectl deploy" in out


def test_generated_password_length(repo):
    r = FakeRunner(dry_run=False)
    provision.init(r, make_args())  # no --password -> generated
    text = (repo / "my-vm.env").read_text()
    pw = [
        line for line in text.splitlines() if line.startswith("export OPENCODE_PASSWORD=")
    ][0]
    assert len(pw.split("=", 1)[1].strip("'")) == 48


def test_existing_env_file_never_overwritten(repo):
    (repo / "my-vm.env").write_text("export FLY_APP=keep\n")
    r = FakeRunner(dry_run=False)
    with pytest.raises(ConfigError, match="--env-file|delete it yourself"):
        provision.init(r, make_args(password="secret123"))
    assert (repo / "my-vm.env").read_text() == "export FLY_APP=keep\n"
    assert r.calls == []  # error raised BEFORE any fly call


# --- region: recorded in the env file; fly.toml is never mutated -----------


def test_region_flag_recorded_in_env_file(repo):
    r = FakeRunner(dry_run=False)
    assert provision.init(r, make_args(password="secret123", region="fra")) == 0
    env = (repo / "my-vm.env").read_text()
    assert "export FLY_REGION='fra'" in env or "export FLY_REGION=fra" in env
    assert (repo / "fly.toml").read_text() == TOML  # checkout byte-identical


def test_region_defaults_from_fly_toml(repo, capsys):
    r = FakeRunner(dry_run=True)
    provision.init(r, make_args())
    assert "--region iad" in capsys.readouterr().out  # volume step uses it
    r2 = FakeRunner(dry_run=False)
    provision.init(r2, make_args(password="secret123"))
    assert "FLY_REGION" in (repo / "my-vm.env").read_text()


def test_fl_region_env_does_not_affect_init(repo, monkeypatch):
    # FLY_REGION is a DEPLOY input (read by config from the sourced env);
    # init's own resolution is --region > fly.toml > default.
    monkeypatch.setenv("FLY_REGION", "syd")
    r = FakeRunner(dry_run=False)
    provision.init(r, make_args(password="secret123"))
    assert "export FLY_REGION=iad" in (repo / "my-vm.env").read_text()


# --- temp-copy deploy ------------------------------------------------------


def test_deploy_uses_temp_copy_never_touches_checkout(repo, monkeypatch):
    make_deploy_files(repo)
    work = repo / "deploy-copy"

    def fake_mkdtemp(*a, **k):
        work.mkdir(parents=True, exist_ok=True)
        return str(work)

    monkeypatch.setattr(provision.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(provision.shutil, "rmtree", lambda *a, **k: None)
    try:
        r = FakeRunner(dry_run=False)
        rc = provision.init(r, make_args(password="secret123", deploy=True, region="fra"))
        assert rc == 0
        assert r.calls[-1][:2] == ["deploy", "-a"]
        assert r.cwds[-1] == str(work)  # fly ran FROM the copy
        copy_toml = (work / "fly.toml").read_text()
        assert 'primary_region = "fra"' in copy_toml  # copy patched...
        assert 'dockerfile = "Dockerfile"' in copy_toml
        assert (repo / "fly.toml").read_text() == TOML  # ...checkout untouched
        assert (work / "Dockerfile").exists()  # context was copied
        assert "secret123" not in (work / "fly.toml").read_text()
    finally:
        work.exists() and shutil.rmtree(work)


def test_deploy_without_region_deploys_checkout_default(repo, monkeypatch):
    make_deploy_files(repo)
    work = repo / "deploy-copy"

    def fake_mkdtemp(*a, **k):
        work.mkdir(parents=True, exist_ok=True)
        return str(work)

    monkeypatch.setattr(provision.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(provision.shutil, "rmtree", lambda *a, **k: None)
    try:
        r = FakeRunner(dry_run=False)
        assert deploy_from(r, None) == 0
        assert (work / "fly.toml").read_text() == TOML  # no patch when no FLY_REGION
    finally:
        work.exists() and shutil.rmtree(work)


def test_missing_deploy_files_errors(repo):
    r = FakeRunner(dry_run=False)
    with pytest.raises(ConfigError, match="missing deploy files"):
        deploy_from(r, None)
    assert r.calls == []


def test_failed_deploy_keeps_finalized_env_file(repo):
    make_deploy_files(repo)
    r = FakeRunner(dry_run=False)

    def failing_deploy(args: list[str], cwd: str | None = None) -> int:
        r.calls.append(args)
        return 1 if args[0] == "deploy" else 0

    r.run = failing_deploy
    rc = provision.init(r, make_args(password="secret123", deploy=True))
    assert rc == 1
    text = (repo / "my-vm.env").read_text()
    assert "export OPENCODE_PASSWORD=secret123" in text  # kept: app+secrets exist


def test_missing_fly_toml_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = FakeRunner()
    with pytest.raises(ConfigError, match="cannot read"):
        provision.init(r, make_args())
