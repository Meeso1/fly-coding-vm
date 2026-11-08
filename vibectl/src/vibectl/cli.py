"""vibectl CLI entrypoint: local setup (`init`) and control of a coding VM on Fly."""

from __future__ import annotations

import argparse
import os
import shlex
import signal
import subprocess
import sys
import webbrowser

from . import __version__
from .api import prompt, sessions
from .config import ConfigError, load
from .fly import Runner, proxy_up
from .provision import init as init_cmd

_PASSWORD_COMMANDS = frozenset({"connect", "proxy", "prompt", "sessions"})


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vibectl",
        description="Set up and control a coding VM on Fly (runs where flyctl is installed).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the fly/curl commands instead of running them (no fly/network needed)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser(
        "init",
        help="one-time setup for a NEW app: create app + volume + secrets, write <app>.env",
    )
    p_init.add_argument("app", help="Fly app name (globally unique)")
    p_init.add_argument(
        "--region",
        help="region for machine + volume (default: fly.toml's primary_region); "
        "differs from it -> fly.toml is rewritten",
    )
    p_init.add_argument("--volume-size", type=int, default=5, help="volume GB (default: 5)")
    p_init.add_argument(
        "--config", default="fly.toml", help="fly.toml path (default: ./fly.toml)"
    )
    p_init.add_argument(
        "--env-file", help="env file to write (default: <app>.env); source it before other commands"
    )
    p_init.add_argument(
        "--password", help="reuse an explicit OPENCODE_PASSWORD instead of generating one"
    )
    p_init.add_argument(
        "--github-app-id", help="GitHub App id; default: $GITHUB_APP_ID"
    )
    p_init.add_argument(
        "--pem", help="path to the GitHub App private key (.pem); default: $GITHUB_APP_PRIVATE_KEY"
    )
    p_init.add_argument(
        "--openrouter-key", help="OpenRouter key; default: $OPENROUTER_API_KEY"
    )
    p_init.add_argument("--force", action="store_true", help="overwrite an existing env file")
    p_init.add_argument(
        "--deploy", action="store_true", help="also run `fly deploy` after the setup steps"
    )

    sub.add_parser("start", help="start the machine + wait until started")
    sub.add_parser("stop", help="stop the machine")
    sub.add_parser("status", help="fly machine status")
    p_logs = sub.add_parser("logs", help="fly logs (or --watcher for watcher.log)")
    p_logs.add_argument("--watcher", action="store_true", help="tail watcher.log via fly machine exec")
    p_logs.add_argument("--lines", type=int, default=100)
    p_exec = sub.add_parser(
        "exec", help="run a command on the VM (ONE string after --, no shell)"
    )
    p_exec.add_argument("cmd", nargs=argparse.REMAINDER, help="remote command; put -- before it")
    sub.add_parser("password", help="print OPENCODE_PASSWORD recovered from the VM")
    p_connect = sub.add_parser(
        "connect", aliases=["proxy"], help="own a fly proxy + run the opencode TUI (or --web)"
    )
    p_connect.add_argument("--web", action="store_true", help="open web UI; Ctrl-C stops the proxy")
    p_prompt = sub.add_parser("prompt", help="create a session + POST a prompt through the proxy")
    p_prompt.add_argument("text", help="prompt text")
    p_prompt.add_argument("--title", default="vibectl prompt")
    p_prompt.add_argument("--dir", dest="directory", default="/root")
    p_sessions = sub.add_parser("sessions", help="list sessions (GET /api/session)")
    p_sessions.add_argument("--json", action="store_true", help="print raw JSON")
    sub.add_parser("deploy", help="fly deploy -a $FLY_APP")
    return parser


def _connect(args: argparse.Namespace, r: Runner) -> int:
    url = f"http://127.0.0.1:{r.cfg.port}"
    with proxy_up(r):
        if args.web:
            print(f"Web UI: {url}  (password = $OPENCODE_PASSWORD); Ctrl-C stops the proxy")
            if r.cfg.dry_run:
                print(f"[dry-run] (would open {url} in a browser and hold the proxy)")
            else:
                webbrowser.open(url)
                try:
                    signal.pause()
                except KeyboardInterrupt:
                    pass
        else:
            oc_cmd = ["opencode", "--server", url]
            if r.cfg.dry_run:
                print("[dry-run] OPENCODE_PASSWORD=$OPENCODE_PASSWORD " + shlex.join(oc_cmd))
            else:
                env = {**os.environ, "OPENCODE_PASSWORD": r.cfg.password or ""}
                subprocess.run(oc_cmd, env=env, check=False)
    # leaving the context terminates the proxy child
    return 0


def _dispatch(args: argparse.Namespace, r: Runner) -> int:
    cmd = args.command
    if cmd == "init":
        return init_cmd(r, args)
    if cmd == "start":
        mid = r.machine_id()
        rc = r.run(["machine", "start", mid, "-a", r.cfg.app])
        if rc != 0:
            return rc
        return r.run(["machine", "wait", mid, "-a", r.cfg.app, "--state", "started"])
    if cmd == "stop":
        return r.run(["machine", "stop", r.machine_id(), "-a", r.cfg.app])
    if cmd == "status":
        mid = r.machine_id()
        print(f"app={r.cfg.app} machine={mid}", file=sys.stderr)
        return r.run(["machine", "status", mid, "-a", r.cfg.app])
    if cmd == "logs":
        if args.watcher:
            return int(
                r.machine_exec(["tail", "-n", str(args.lines), "/data/logs/watcher.log"])
            )
        return r.run(["logs", "-a", r.cfg.app])
    if cmd == "exec":
        remote = list(args.cmd)
        if remote and remote[0] == "--":
            remote = remote[1:]
        if not remote:
            raise ConfigError(
                "usage: vibectl exec -- <command> [args...]  (ONE string after --, no shell)"
            )
        return int(r.machine_exec(remote))
    if cmd == "password":
        out = r.machine_exec(["cat", "/data/state/opencode_password"], capture=True)
        if not r.cfg.dry_run:
            print(out)
        return 0
    if cmd in ("connect", "proxy"):
        return _connect(args, r)
    if cmd == "prompt":
        prompt(r, args.text, title=args.title, directory=args.directory)
        return 0
    if cmd == "sessions":
        sessions(r, as_json=args.json)
        return 0
    if cmd == "deploy":
        print("note: fly deploy starts the machine; run `vibectl stop` if idle.", file=sys.stderr)
        return r.run(["deploy", "-a", r.cfg.app])
    return 2


def main(argv: list[str] | None = None) -> int:
    """Parse args, load env-only config, dispatch; return the process exit code."""
    args = _parser().parse_args(argv)
    try:
        # `vibectl init <app>` carries the app as its CLI argument; it must work
        # before any env file exists, so no FLY_APP requirement there.
        cfg = load(
            args.dry_run,
            need_password=args.command in _PASSWORD_COMMANDS,
            app=args.app if args.command == "init" else None,
        )
        return _dispatch(args, Runner(cfg))
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
