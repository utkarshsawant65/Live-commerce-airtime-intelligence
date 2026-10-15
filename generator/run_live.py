"""
"Simulated live" micro-batch loop.

This is the industry-standard "live" pattern for a warehouse: a scheduled generator
emits a fresh micro-batch, the loader appends it to RAW, and an incremental dbt run
re-models only the new rows. NOT Kafka/streaming — that's overkill and costs money for
a near-real-time warehouse use case. The natural "more real-time" upgrade is Snowflake
Streams + Tasks / Dynamic Tables, not Kafka.

Each iteration:
    1. generate a small new batch for the *next* time window, with non-colliding ids
    2. PUT + COPY INTO RAW (append)
    3. (optionally) run `dbt build --select state:modified+` to re-model incrementally

Usage:
    python generator/run_live.py --iterations 6 --interval 60 --run-dbt
    python generator/run_live.py --iterations 3 --interval 0 --no-load   # dry, files only
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


def _next_window_start(out_dir: str, default_start: datetime) -> tuple[datetime, int]:
    """Continue from where the last manifest left off so batches don't overlap."""
    manifest = os.path.join(out_dir, "_batch_manifest.json")
    if os.path.exists(manifest):
        with open(manifest, encoding="utf-8") as f:
            m = json.load(f)
        last_start = datetime.strptime(m["window_start_utc"], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc)
        return last_start + timedelta(days=m["days"]), int(m["max_order_id"]) + 1
    return default_start, 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Looped micro-batch generator = simulated live.")
    ap.add_argument("--out", default="generator/output")
    ap.add_argument("--iterations", type=int, default=6)
    ap.add_argument("--interval", type=int, default=60, help="seconds between batches")
    ap.add_argument("--batch-days", type=int, default=1, help="days of data per micro-batch")
    ap.add_argument("--start", default="2026-06-10")
    ap.add_argument("--seed", type=int, default=1000)
    ap.add_argument("--run-dbt", action="store_true", help="run incremental dbt after each load")
    ap.add_argument("--no-load", action="store_true", help="generate files only; skip Snowflake load")
    args = ap.parse_args()

    default_start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    for i in range(args.iterations):
        start, seq = _next_window_start(args.out, default_start)
        seed = args.seed + i
        print(f"[live {i + 1}/{args.iterations}] window={start.date()} "
              f"days={args.batch_days} seed={seed} seq_start={seq}")
        summary = gb.generate(args.out, start, args.batch_days, seed,
                              n_products=120, n_presenters=10, order_seq_start=seq)
        print(f"  generated {summary['counts']['order_lines']} order lines")

        if not args.no_load:
            # Append (no --truncate) so the warehouse accumulates a growing history.
            rc = subprocess.call([sys.executable, os.path.join(os.path.dirname(__file__),
                                                               "load_to_snowflake.py"),
                                  "--in", args.out])
            if rc != 0:
                print("  load failed; stopping loop")
                break

        if args.run_dbt:
            # Incremental re-model of just what changed; lookback re-attributes late orders.
            subprocess.call(["dbt", "build", "--select", "state:modified+"], cwd="dbt")

        if i < args.iterations - 1 and args.interval > 0:
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
