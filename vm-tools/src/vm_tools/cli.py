"""vm-tools CLI entrypoint."""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .github_app import mint_token
from .work import save_token, start_work


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="vm-tools",
        description="Small CLI tools for a Fly.io coding VM.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_mint = sub.add_parser(
        "mint-gh-token",
        help="Mint a 1h GitHub App installation token scoped to ONE repo; prints it.",
    )
    p_mint.add_argument("repo", help="owner/repo")
    p_mint.add_argument(
        "--save",
        action="store_true",
        help="also store the token (0600) in $VM_STATE_DIR/gh_token (default /data/state)",
    )

    p_work = sub.add_parser(
        "start-work",
        help="Clone/update owner/repo into $VM_WORK_DIR/<repo> (default /root/work) "
        "and configure git auth. Prints the repo path.",
    )
    p_work.add_argument("repo", help="owner/repo")
    p_work.add_argument("branch", nargs="?", default=None, help="branch to check out")

    p_watch = sub.add_parser(
        "watch",
        help="Idle watcher: stop this machine when no OpenCode session is busy "
        "(a connected client keeps it awake), recording per-run LLM cost.",
    )
    p_watch.add_argument("--idle-minutes", type=int, default=int(os.environ.get("IDLE_MINUTES", "15")))
    p_watch.add_argument(
        "--max-uptime-hours", type=int, default=int(os.environ.get("MAX_UPTIME_HOURS", "12"))
    )
    p_watch.add_argument("--port", type=int, default=int(os.environ.get("OC_PORT", "4096")))
    p_watch.add_argument("--poll-seconds", type=int, default=30)

    args = parser.parse_args(argv)

    if args.command == "mint-gh-token":
        token = mint_token(args.repo)
        if args.save:
            path = save_token(args.repo, token=token)
            print(f"saved to {path}", file=sys.stderr)
        print(token)
        return 0

    if args.command == "start-work":
        dest = start_work(args.repo, args.branch)
        print(dest)
        print(
            'gh CLI: export GITHUB_TOKEN="$(cat "$VM_STATE_DIR/gh_token" 2>/dev/null || cat /data/state/gh_token)"',
            file=sys.stderr,
        )
        return 0

    if args.command == "watch":
        from . import watch

        return watch.run(
            idle_minutes=args.idle_minutes,
            max_uptime_hours=args.max_uptime_hours,
            port=args.port,
            poll_seconds=args.poll_seconds,
        )

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
