"""``vibectl init``: one-time provisioning of a NEW coding VM on Fly.

Runs the setup steps that used to be manual in the README:

    fly apps create <app>
    fly volumes create data --region <region> --size <n> -a <app>
    fly secrets set -a <app> OPENCODE_PASSWORD=... [GITHUB_APP_ID=...]
                             [GITHUB_APP_PRIVATE_KEY=...]
                             [OPENROUTER_API_KEY=...]
    ([fly deploy] only with --deploy)

plus writes ``<app>.env`` (default ``<app>.env`` in the *invoking* working
directory, ``--env-file`` to override; never inside the temp deploy dir) with
``export FLY_APP=...``, ``export FLY_REGION=...`` and a generated
``OPENCODE_PASSWORD`` for ``source <app>.env`` before any other vibectl
command.

Region resolution: ``--region`` beats the checkout ``fly.toml``'s
``primary_region`` beats :data:`DEFAULT_REGION`. The chosen region is
*recorded in the env file* (``FLY_REGION``): every deploy — ``init --deploy``
and ``vibectl deploy`` — runs ``fly deploy`` from a fresh temp copy of
:data:`DEPLOY_FILES` and patches only that copy's fly.toml, so the checkout is
never mutated and parallel deploys/inits from one directory are safe.

Env-file lifecycle (never overwritten, no overwrite flag): the path is
claimed atomically (``O_CREAT|O_EXCL``) BEFORE any fly call with **every line
commented out** — sourcing the file mid-provision must not export anything.
Only after the fly steps succeed it is atomically replaced with the real
``export`` lines; a failure of any pre-finalize step deletes the claim so e.g.
a failed login cannot block the retry after ``fly auth login``. A deploy
failure-after-finalize keeps the file (the Fly-side app/volume/secrets exist).

Credentials come from flags or the current environment only — vibectl never
reads ``.env`` files itself. NOT idempotent: use a fresh, unused app name.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import secrets
import shlex
import shutil
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

from .config import ConfigError, require_fly
from .fly import Runner

Args = argparse.Namespace

DEFAULT_REGION = "iad"

# Build context flyctl needs: exactly what the Dockerfile COPYs + fly.toml.
DEPLOY_FILES = ("fly.toml", "Dockerfile", "entrypoint.sh", "vm-tools", "skills", "home")

SECRET_NAMES = (
    "OPENCODE_PASSWORD",
    "GITHUB_APP_ID",
    "GITHUB_APP_PRIVATE_KEY",
    "OPENROUTER_API_KEY",
)


# --- fly.toml helpers (defaults only; the checkout is never mutated) --------


def read_top_level(path: Path) -> dict[str, str]:
    """Top-level ``key = "value"`` pairs of fly.toml (``app``, ``primary_region``).

    Stops at the first ``[section]`` header, so keys like ``dockerfile`` inside
    ``[build]`` are excluded.
    """
    try:
        text = path.read_text()
    except OSError as exc:
        raise ConfigError(
            f"cannot read {path}: {exc} — run `vibectl init` from a checkout of this repo"
        ) from exc
    out: dict[str, str] = {}
    for line in text.splitlines():
        if line.lstrip().startswith("["):
            break
        m = re.match(r'\s*([A-Za-z_][\w-]*)\s*=\s*"(.*)"\s*$', line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def patch_primary_region(text: str, region: str) -> str:
    """Rewrite the fly.toml text's ``primary_region = "..."`` line."""
    if not re.search(r'(?m)^\s*primary_region\s*=\s*"', text):
        raise ConfigError(f"no primary_region = ... line found to set to {region!r}")
    return re.sub(
        r'(?m)^(\s*primary_region\s*=\s*)"[^"]*"',
        lambda m: f'{m.group(1)}"{region}"',
        text,
        count=1,
    )


# --- throwaway deploy context ----------------------------------------------


@contextlib.contextmanager
def _temp_deploy_dir() -> Iterator[Path]:
    """Copy :data:`DEPLOY_FILES` from the checkout into a fresh /tmp dir.

    Yields the temp dir and removes it afterwards, whatever happens: fly
    always builds from the copy, so the checkout stays byte-identical and two
    concurrent deploys cannot share any mutable file. Errors clearly when run
    outside a checkout.
    """
    src = Path.cwd()
    missing = [f for f in DEPLOY_FILES if not (src / f).exists()]
    if missing:
        raise ConfigError(
            f"missing deploy files in {src}: {', '.join(missing)} — run `vibectl "
            "deploy`/`init --deploy` from a checkout of this repo"
        )
    tmp = Path(tempfile.mkdtemp(prefix="vibectl-deploy-"))
    try:
        for name in DEPLOY_FILES:
            s, d = src / name, tmp / name
            if s.is_dir():
                shutil.copytree(s, d)
            else:
                shutil.copy2(s, d)
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def deploy_from(r: Runner, region: str | None) -> int:
    """``fly deploy`` from a fresh temp copy of :data:`DEPLOY_FILES`.

    ``region`` ($FLY_REGION or the ``--region`` given to init) is applied to
    the *copy's* fly.toml only; passing None deploys with the checkout's
    ``primary_region`` default. The temp dir is removed afterwards.
    """
    if r.cfg.dry_run:
        if region is not None:
            print(
                f'[dry-run] (copy the deploy files to a temp dir, set fly.toml '
                f'primary_region = "{region}" (= $FLY_REGION), fly deploy from there '
                "and remove the temp dir after)"
            )
        print("note: fly deploy starts the machine; run `vibectl stop` if idle.",
              file=sys.stderr)
        return r.run(["deploy", "-a", r.cfg.app])

    require_fly(False)
    with _temp_deploy_dir() as work:
        if region is not None:
            toml = work / "fly.toml"
            toml.write_text(patch_primary_region(toml.read_text(), region))
            print(f'deploying from a temp copy: fly.toml primary_region = "{region}" ')
        print("note: fly deploy starts the machine; run `vibectl stop` if idle.",
              file=sys.stderr)
        return r.run(["deploy", "-a", r.cfg.app], cwd=str(work))


# --- env file: reserve (all commented) -> finalize (atomic) / discard -------


def env_file_path(args: Args, app: str) -> Path:
    """Where the ``source``-able env file is written (default ``<app>.env``)."""
    return Path(args.env_file or f"{app}.env")


def _final_env_text(app: str, password: str, region: str | None) -> str:
    q = shlex.quote
    lines = [
        f"# written by `vibectl init {app}` — source before other vibectl commands:",
        f"#   source {app}.env",
        "",
        f"export FLY_APP={q(app)}",
    ]
    if region is not None:
        lines.append(f"export FLY_REGION={q(region)}")
    lines += [
        f"export OPENCODE_PASSWORD={q(password)}",
        "",
        "# export OC_PORT=4096           # optional local proxy port pin; default:"
        " auto free port (recorded in <app>.port)",
        "# export FLY_MACHINE_ID=        # optional; otherwise auto-resolved",
    ]
    return "\n".join(lines) + "\n"


def reserve_env_file(path: Path, app: str, region: str | None) -> None:
    """Atomically claim ``path`` (O_CREAT|O_EXCL, 0600) with all-commented content.

    Called BEFORE any fly command: sourcing the file mid-provision must not
    export anything, so every line is commented out. Also fails fast on an
    existing file — before a single fly call is made — naming both remedies;
    an env file is NEVER overwritten.
    """
    q = shlex.quote
    provisional = (
        f"# written by `vibectl init {app}` — provisioning in progress, do NOT source yet.\n"
        f"# Every line is commented out; after the Fly steps succeed this file is\n"
        f"# atomically replaced with the real exports (never sourced mid-run).\n"
        f"#\n"
        f"# export FLY_APP={q(app)}\n"
        + (f"# export FLY_REGION={q(region)}\n" if region is not None else "")
        + f"# export OPENCODE_PASSWORD='(pending)'\n"
        f"#\n"
        f"# # export OC_PORT=4096         # optional local proxy port pin\n"
        f"# # export FLY_MACHINE_ID=      # optional; otherwise auto-resolved\n"
    )
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ConfigError(
            f"{path} already exists — delete it yourself to retry, or pass "
            "--env-file <path> to write a fresh one elsewhere"
        ) from exc
    with os.fdopen(fd, "w") as f:
        f.write(provisional)


def finalize_env_file(
    path: Path, app: str, password: str, region: str | None
) -> None:
    """Atomically replace the provisional file with the real one (0600 kept)."""
    body = _final_env_text(app, password, region)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:  # mkstemp already creates with mode 0600
            f.write(body)
        os.replace(tmp_name, path)
    finally:
        leftover = Path(tmp_name)
        if leftover.exists():
            leftover.unlink()


def discard_env_file(path: Path) -> None:
    """Remove the reservation (only called when init created it this run)."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass


# --- init -------------------------------------------------------------------


def _credential_pairs(args: Args) -> dict[str, str]:
    """Extra secret pairs from flags (falling back to the environment).

    Missing credentials are skipped with a printed note — they can be added
    later with plain ``fly secrets set -a <app> KEY=...``.
    """
    pairs: dict[str, str] = {}
    pem = Path(args.pem).read_text().strip() if args.pem else None
    wanted = (
        ("GITHUB_APP_ID", args.github_app_id or os.environ.get("GITHUB_APP_ID"), "--github-app-id"),
        (
            "GITHUB_APP_PRIVATE_KEY",
            pem or os.environ.get("GITHUB_APP_PRIVATE_KEY"),
            "--pem FILE",
        ),
        (
            "OPENROUTER_API_KEY",
            args.openrouter_key or os.environ.get("OPENROUTER_API_KEY"),
            "--openrouter-key",
        ),
    )
    for name, value, flag in wanted:
        if value:
            pairs[name] = value
        else:
            print(f"note: {flag} / ${name} not given — skipping {name} "
                  "(add later with: fly secrets set)", flush=True)
    return pairs


def _run(r: Runner, cmd: list[str]) -> None:
    """Run one fly step, stopping init with a clear error on failure."""
    rc = r.run(cmd)
    if rc != 0:
        name = cmd[0] if len(cmd) < 2 else f"{cmd[0]} {cmd[1]}"
        raise ConfigError(f"`{name}` failed with exit {rc} — resolve it and rerun init")


def init(r: Runner, args: Args) -> int:
    """Provision the app, set secrets, finalize the env file, maybe deploy."""
    cfg = r.cfg
    dry = cfg.dry_run
    require_fly(dry)

    top = read_top_level(Path("fly.toml"))
    region = args.region or top.get("primary_region") or DEFAULT_REGION
    password = args.password if args.password else (
        secrets.token_hex(24) if not dry else None
    )
    env_file = env_file_path(args, cfg.app)
    vol_cmd = [
        "volumes", "create", "data",
        "--region", region,
        "--size", str(args.volume_size),
        "-a", cfg.app,
    ]

    if dry:
        _run(r, ["apps", "create", cfg.app])
        _run(r, vol_cmd)
        pairs = {n: "$" + n for n in SECRET_NAMES}  # placeholders, never real values
        _run(r, ["secrets", "set", "-a", cfg.app] + [f"{k}={v}" for k, v in pairs.items()])
        print(
            f"[dry-run] (would write {env_file}: FLY_APP={cfg.app}, "
            f"FLY_REGION={region} and a generated OPENCODE_PASSWORD, mode 0600; "
            "never overwritten — pass --env-file if it exists already)"
        )
        if args.deploy:
            deploy_from(r, region)
        print("next: rerun without --dry-run for real (writes no files)")
        return 0

    # Reserve the env file BEFORE any fly command (atomic claim; all lines
    # commented out). An existing file is an immediate error here, so no fly
    # call has been made when it happens.
    reserve_env_file(env_file, cfg.app, region)
    try:
        _run(r, ["apps", "create", cfg.app])
        _run(r, vol_cmd)
        pairs = {"OPENCODE_PASSWORD": password, **_credential_pairs(args)}
        _run(r, ["secrets", "set", "-a", cfg.app] + [f"{k}={v}" for k, v in pairs.items()])
    except ConfigError:
        discard_env_file(env_file)  # failed login must not block the retry
        raise
    finalize_env_file(env_file, cfg.app, password, region)
    print(f"finalized {env_file} (0600): source it before other vibectl commands:\n"
          f"  source {env_file}")

    if args.deploy:
        return deploy_from(r, region)  # deploy failure KEEPS the file: app+secrets exist
    print("next: `vibectl deploy`")
    return 0
