"""Show per-VM-run LLM cost recorded by the watcher on the VM's /data volume."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from .fly import Runner

METRICS_PATH = "/data/state/metrics.jsonl"


def _fmt_ts(value: object) -> str:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).strftime(  # type: ignore[arg-type]
            "%Y-%m-%d %H:%M:%S UTC"
        )
    except (TypeError, ValueError, OSError, OverflowError):
        return "?"


def _fmt_cost(value: object) -> str:
    try:
        return f"${float(value):.4f}"  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "$0.0000"


def show_metrics(r: Runner) -> None:
    """Print each past VM run from /data/state/metrics.jsonl (via fly exec)."""
    raw = r.machine_exec(["cat", METRICS_PATH], capture=True)
    if r.cfg.dry_run:
        return  # machine_exec already printed the fly equivalent
    records: list[dict] = []
    for line in str(raw).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not records:
        print("(no VM runs recorded yet)")
        return
    for rec in records:
        print(
            f"run {_fmt_ts(rec.get('started_at'))} -> {_fmt_ts(rec.get('stopped_at'))}  "
            f"reason={rec.get('reason', '?')}  "
            f"llm_cost_usd={_fmt_cost(rec.get('llm_cost_usd'))}"
        )
        for s in rec.get("sessions") or []:
            print(
                f"    {s.get('id', '')}  {_fmt_cost(s.get('cost'))}  {s.get('title', '')}"
            )
