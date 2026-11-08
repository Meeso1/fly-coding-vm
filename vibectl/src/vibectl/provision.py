"""``vibectl init``: one-time provisioning of a NEW coding VM on Fly.

Runs the setup steps that used to be manual in the README:

    fly apps create <app>
    fly volumes create data --region <region> --size <n> -a <app>
    fly secrets set -a <app> OPENCODE_PASSWORD=... [GITHUB_APP_ID=...]
                             [GITHUB_APP_PRIVATE_KEY=...]
                             [OPENROUTER_API_KEY=...]
    ([fly deploy -a <app>] only with --deploy)

plus writes ``<app>.env`` (via ``--env-file``, default ``<app>.env``) with
``export FLY_APP=...`` and a generated ``OPENCODE_PASSWORD`` for
``source <app>.env`` before any other vibectl command. ``--region`` that
differs from the ``fly.toml`` default rewrites its ``primary_region`` line
(deploy takes the machine's region from there).

Credentials come from flags or the current environment only — vibectl never
reads ``.env`` files itself. NOT idempotent: use a fresh, unused app name.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import shlex
from pathlib import Path

from .config import ConfigError, require_fly
from .fly import Runner

Args = argparse.Namespace

DEFAULT_REGION = "iad"

SECRET_NAMES = (
    "OPENCODE_PASSWORD",
    "GITHUB_APP_ID",
    "GITHUB_APP_PRIVATE_KEY",
    "OPENROUTER_API_KEY",
)


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
    new = re.sub(
        r'(?m)^(\s*primary_region\s*=\s*)"[^"]*"',
        lambda m: f'{m.group(1)}"{region}"',
        text,
        count=1,
    )
    if new == text:
        raise ConfigError(f"no primary_region = ... line found to set to {region!r}")
    return new


def env_file_path(args: Args, app: str) -> Path:
    """Where the ``source``-able env file is written (default ``<app>.env``)."""
    return Path(args.env_file or f"{app}.env")


def write_env_file(path: Path, app: str, password: str, *, force: bool) -> None:
    """Write the source-able env file with mode 0600; refuse to clobber."""
    if path.exists() and not force:
        raise ConfigError(f"{path} already exists; pass --force to overwrite it")
    q = shlex.quote
    text = (
        f"# written by `vibectl init {app}` — source before other vibectl commands:\n"
        f"#   source {path}\n"
        "\n"
        f"export FLY_APP={q(app)}\n"
        f"export OPENCODE_PASSWORD={q(password)}\n"
        "\n"
        "# export OC_PORT=4096             # optional local proxy port\n"
        "# export FLY_MACHINE_ID=          # optional; otherwise auto-resolved\n"
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


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
    """Provision the app, set secrets, write the env file, maybe deploy."""
    cfg = r.cfg
    dry = cfg.dry_run
    require_fly(dry)

    toml_path = Path(args.config)
    top = read_top_level(toml_path)
    region = args.region or top.get("primary_region") or DEFAULT_REGION
    password = args.password if args.password else (
        secrets.token_hex(24) if not dry else None
    )

    _run(r, ["apps", "create", cfg.app])
    _run(
        r,
        [
            "volumes", "create", "data",
            "--region", region,
            "--size", str(args.volume_size),
            "-a", cfg.app,
        ],
    )
    if dry:
        pairs = {n: "$" + n for n in SECRET_NAMES}
    else:
        pairs = {"OPENCODE_PASSWORD": password, **_credential_pairs(args)}
    _run(r, ["secrets", "set", "-a", cfg.app] + [f"{k}={v}" for k, v in pairs.items()])

    current = top.get("primary_region")
    if args.region and args.region != current:
        if dry:
            print(f'[dry-run] (would rewrite {toml_path}: primary_region = "{args.region}")')
        else:
            toml_path.write_text(patch_primary_region(toml_path.read_text(), args.region))
            print(f'updated {toml_path}: primary_region = "{args.region}"')

    env_file = env_file_path(args, cfg.app)
    if dry:
        print(
            f"[dry-run] (would write {env_file} with FLY_APP={cfg.app} and a generated "
            f"OPENCODE_PASSWORD, mode 0600)"
        )
    else:
        write_env_file(env_file, cfg.app, password, force=args.force)

    if args.deploy:
        _run(r, ["deploy", "-a", cfg.app])

    if dry:
        print("next: rerun without --dry-run for real (writes no files)")
    else:
        print(f"wrote {env_file} (0600): source it before other vibectl commands:\n"
              f"  source {env_file}")
        if not args.deploy:
            print("next: `vibectl deploy`")
    return 0
