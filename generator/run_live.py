"""
Simulated live feed: generate a micro-batch for the next time window, append it to RAW,
optionally run dbt, repeat.

Usage:
    python generator/run_live.py --iterations 6 --interval 60 --run-dbt
    python generator/run_live.py --iterations 3 --interval 0 --no-load   # files only
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import generate_batch as gb

HERE = os.path.dirname(os.path.abspath(__file__))
DBT_DIR = os.path.join(os.path.dirname(HERE), "dbt")


def _next_window_start(out_dir: str, default_start: datetime) -> tuple[datetime, int, int]:
    """Return (window start, order id offset, segment id offset) following the last batch."""
    manifest = os.path.join(out_dir, "_batch_manifest.json")
    if os.path.exists(manifest):
        with open(manifest, encoding="utf-8") as f:
            m = json.load(f)
        last_start = datetime.strptime(m["window_start_utc"], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc)
        return (last_start + timedelta(days=m["days"]),
                int(m["max_order_id"]),
                int(m.get("max_segment_id", 0)))
    return default_start, 0, 0


def _run_dbt() -> int:
    cmd = ["dbt", "build"]
    if not os.environ.get("DBT_PROFILES_DIR"):
        cmd += ["--profiles-dir", DBT_DIR]
    return subprocess.call(cmd, cwd=DBT_DIR)


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate and load micro-batches in a loop.")
    ap.add_argument("--out", default="generator/output")
    ap.add_argument("--iterations", type=int, default=6)
    ap.add_argument("--interval", type=int, default=60, help="seconds between batches")
    ap.add_argument("--batch-days", type=int, default=1, help="days of data per micro-batch")
    ap.add_argument("--start", default="2026-06-10")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--products", type=int, default=120)
    ap.add_argument("--presenters", type=int, default=10)
    ap.add_argument("--run-dbt", action="store_true", help="run dbt build after each load")
    ap.add_argument("--no-load", action="store_true", help="generate files only; skip Snowflake load")
    args = ap.parse_args()

    default_start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    for i in range(args.iterations):
        start, order_seq, segment_seq = _next_window_start(args.out, default_start)
        # Fact seed follows the window, so a restarted loop doesn't replay earlier batches.
        # Dimensions use the base seed and start date so they stay identical across batches.
        seed = args.seed + (start - default_start).days
        print(f"[live {i + 1}/{args.iterations}] window={start.date()} days={args.batch_days} "
              f"seed={seed} order_seq={order_seq} segment_seq={segment_seq}")
        summary = gb.generate(args.out, start, args.batch_days, seed,
                              n_products=args.products, n_presenters=args.presenters,
                              order_seq_start=order_seq, segment_seq_start=segment_seq,
                              dim_seed=args.seed, dim_start=default_start,
                              dim_days=args.iterations * args.batch_days)
        print(f"  generated {summary['counts']['order_lines']} order lines")

        if not args.no_load:
            # No --truncate: each batch appends to RAW.
            rc = subprocess.call([sys.executable, os.path.join(HERE, "load_to_snowflake.py"),
                                  "--in", args.out])
            if rc != 0:
                print("  load failed; stopping loop")
                break

        if args.run_dbt:
            if _run_dbt() != 0:
                print("  dbt build failed; stopping loop")
                break

        if i < args.iterations - 1 and args.interval > 0:
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
