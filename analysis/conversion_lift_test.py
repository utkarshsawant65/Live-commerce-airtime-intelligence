"""
One-sided two-proportion z-test: is online conversion (distinct orders / web sessions)
higher on air than off air?

Counts come from marts.mart_conversion_windows (--from-snowflake) or are recomputed from
the generated CSVs with the same rules: online channels only, and a minute is on air if
it falls in any segment's [start, end + 30 min) window. Each session is treated as an
independent trial, so this is a population-level lift, not a per-user causal estimate.

Usage:
    python analysis/conversion_lift_test.py --in generator/output
    python analysis/conversion_lift_test.py --from-snowflake
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import math
import os
import sys

LAG_MINUTES = 30  # must match dbt var attribution_lag_minutes

Counts = dict[str, dict[str, int]]


def _parse_ts(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)


def _minute(s: str) -> int:
    return int(_parse_ts(s).timestamp()) // 60


def _read_csv(path: str) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def counts_from_csv(in_dir: str) -> Counts:
    """On-air / off-air distinct orders and sessions for online channels, from the CSVs."""
    paths = {name: os.path.join(in_dir, f"{name}.csv")
             for name in ("dim_channel", "fact_airtime_segment", "fact_order_line", "fact_traffic")}
    for p in paths.values():
        if not os.path.exists(p):
            sys.exit(f"Missing {p}. Run the generator first: python generator/generate_batch.py")

    online = {c["channel_id"] for c in _read_csv(paths["dim_channel"])
              if c["channel_type"] == "online"}

    on_air_minutes: set[int] = set()
    for s in _read_csv(paths["fact_airtime_segment"]):
        start = _minute(s["start_ts_utc"])
        end = _minute(s["end_ts_utc"]) + LAG_MINUTES
        on_air_minutes.update(range(start, end))

    orders: dict[str, set[str]] = {"on_air": set(), "off_air": set()}
    for o in _read_csv(paths["fact_order_line"]):
        if o["channel_id"] in online:
            state = "on_air" if _minute(o["event_ts_utc"]) in on_air_minutes else "off_air"
            orders[state].add(o["order_id"])

    sessions = {"on_air": 0, "off_air": 0}
    for t in _read_csv(paths["fact_traffic"]):
        if t["channel_id"] in online:
            state = "on_air" if _minute(t["minute_ts_utc"]) in on_air_minutes else "off_air"
            sessions[state] += int(t["web_sessions"])

    return {state: {"orders": len(orders[state]), "sessions": sessions[state]}
            for state in ("on_air", "off_air")}


def counts_from_snowflake() -> Counts:
    """Sum mart_conversion_windows up to one row per airtime state."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "generator"))
    from load_to_snowflake import connect

    conn = connect(role=os.environ.get("SNOWFLAKE_BI_ROLE", "ROLE_BI_READER"),
                   warehouse=os.environ.get("SNOWFLAKE_BI_WAREHOUSE", "WH_BI"),
                   schema="MARTS")
    try:
        cur = conn.cursor()
        cur.execute(
            "select airtime_state, sum(orders), sum(web_sessions) "
            "from marts.mart_conversion_windows group by airtime_state"
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    return {state: {"orders": int(orders or 0), "sessions": int(sessions or 0)}
            for state, orders, sessions in rows}


def two_proportion_z_test(x1: int, n1: int, x2: int, n2: int) -> tuple[float, float, float, float] | None:
    """One-sided test that p1 > p2. Returns (p1, p2, z, p_value), or None if untestable."""
    if n1 == 0 or n2 == 0:
        return None
    p1 = x1 / n1
    p2 = x2 / n2
    p_pool = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
    if se == 0:
        return None
    z = (p1 - p2) / se
    p_value = 0.5 * math.erfc(z / math.sqrt(2))
    return p1, p2, z, p_value


def main() -> None:
    ap = argparse.ArgumentParser(description="Two-proportion z-test for airtime conversion lift.")
    ap.add_argument("--in", dest="in_dir", default="generator/output")
    ap.add_argument("--from-snowflake", action="store_true",
                    help="read counts from marts.mart_conversion_windows instead of CSVs")
    ap.add_argument("--alpha", type=float, default=0.05, help="significance level")
    args = ap.parse_args()

    counts = counts_from_snowflake() if args.from_snowflake else counts_from_csv(args.in_dir)
    empty = {"orders": 0, "sessions": 0}
    on = counts.get("on_air", empty)
    off = counts.get("off_air", empty)

    print("Two-proportion z-test: online conversion on air vs off air")
    print("=" * 70)
    print(f"  on-air : {on['orders']:>8} orders / {on['sessions']:>10} sessions")
    print(f"  off-air: {off['orders']:>8} orders / {off['sessions']:>10} sessions")
    print("-" * 70)

    result = two_proportion_z_test(on["orders"], on["sessions"], off["orders"], off["sessions"])
    if result is None:
        print("  INCONCLUSIVE: not enough data in one or both states to run the test.")
        print("=" * 70)
        sys.exit(1)

    p_on, p_off, z, p_value = result
    lift_abs = p_on - p_off
    lift_rel = (p_on / p_off - 1) if p_off > 0 else float("inf")
    print(f"  conversion    : on-air {p_on:.5f}, off-air {p_off:.5f}")
    print(f"  absolute lift : {lift_abs:+.5f}  ({lift_abs * 1000:+.2f} per 1,000 sessions)")
    print(f"  relative lift : {lift_rel:+.1%}")
    print(f"  z-statistic   : {z:.3f}")
    print(f"  p-value       : {p_value:.3e}  (one-sided H1: p_on_air > p_off_air)")
    print("-" * 70)
    if p_value < args.alpha:
        print(f"  Reject H0 at alpha={args.alpha}: on-air conversion is significantly higher.")
    else:
        print(f"  Fail to reject H0 at alpha={args.alpha}: no significant lift.")
    print("=" * 70)


if __name__ == "__main__":
    main()
