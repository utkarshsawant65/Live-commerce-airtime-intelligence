"""
Conversion-lift hypothesis test — a single, correct two-proportion z-test.

Question: is the conversion rate DURING airtime significantly higher than the
off-air baseline?

    H0: p_on_air = p_off_air      (airtime has no effect on conversion)
    H1: p_on_air > p_off_air      (airtime lifts conversion) — one-sided

Conversion = distinct orders ÷ web_sessions, computed per airtime_state. The counts
come from mart_conversion_windows (one row per state) when Snowflake is available; for
an offline run we recompute the identical counts from the generated CSVs, so the number
in this test matches the number the dashboard shows.

Why a two-proportion z-test (and only this): the data is two independent groups of
Bernoulli trials (a session converts or not) summarised as counts + denominators — the
textbook setting for comparing two proportions at scale. We report the statistic, the
p-value, and a plain-language conclusion, and we state assumptions honestly. No ML, no
forecasting, no battery of half-justified tests — basic and correct.

Assumptions (stated, not hidden):
  * Each web_session is treated as an independent conversion trial.
  * On-air and off-air sessions are independent samples.
  * Large-sample normal approximation holds (np and n(1-p) >> 5 here).
Caveat: sessions are an aggregate proxy for trials; this is a population-level lift, not
a per-user causal estimate. We do not claim causality beyond the controlled design.

Usage:
    python analysis/conversion_lift_test.py                      # offline, from CSVs
    python analysis/conversion_lift_test.py --from-snowflake     # query the mart
    python analysis/conversion_lift_test.py --in generator/output
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import math
import os
import sys

LAG_MINUTES = 30  # must match the generator / dbt var attribution_lag_minutes


def _parse_ts(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)


def counts_from_csv(in_dir: str) -> dict[str, dict[str, int]]:
    """Recompute on-air / off-air orders and sessions from the generated CSVs, using the
    same on-air rule as the dbt model (a minute is on-air if any segment's [start, end+lag]
    window covers it; an order is on-air if it falls in such a window for its product)."""
    seg_path = os.path.join(in_dir, "fact_airtime_segment.csv")
    ord_path = os.path.join(in_dir, "fact_order_line.csv")
    trf_path = os.path.join(in_dir, "fact_traffic.csv")
    for p in (seg_path, ord_path, trf_path):
        if not os.path.exists(p):
            sys.exit(f"Missing {p}. Run the generator first: python generator/generate_batch.py")

    segments = list(csv.DictReader(open(seg_path, encoding="utf-8")))
    # Set of on-air minutes (epoch//60), any product — for classifying sessions.
    on_air_minutes: set[int] = set()
    # Per-product windows — for classifying orders.
    windows_by_product: dict[str, list[tuple[dt.datetime, dt.datetime]]] = {}
    for s in segments:
        start = _parse_ts(s["start_ts_utc"])
        end = _parse_ts(s["end_ts_utc"]) + dt.timedelta(minutes=LAG_MINUTES)
        windows_by_product.setdefault(s["product_id"], []).append((start, end))
        m = start
        while m < end:
            on_air_minutes.add(int(m.timestamp()) // 60)
            m += dt.timedelta(minutes=1)

    # Orders: on-air if the order time falls in any window for its product.
    on_orders: set[str] = set()
    off_orders: set[str] = set()
    for o in csv.DictReader(open(ord_path, encoding="utf-8")):
        ts = _parse_ts(o["event_ts_utc"])
        wins = windows_by_product.get(o["product_id"], [])
        is_on = any(start <= ts < end for start, end in wins)
        (on_orders if is_on else off_orders).add(o["order_id"])

    # Sessions: only online channels (web_sessions > 0), split by on-air minute.
    on_sessions = off_sessions = 0
    for t in csv.DictReader(open(trf_path, encoding="utf-8")):
        sessions = int(t["web_sessions"])
        if sessions <= 0:
            continue
        minute_key = int(_parse_ts(t["minute_ts_utc"]).timestamp()) // 60
        if minute_key in on_air_minutes:
            on_sessions += sessions
        else:
            off_sessions += sessions

    return {
        "on_air": {"orders": len(on_orders), "sessions": on_sessions},
        "off_air": {"orders": len(off_orders), "sessions": off_sessions},
    }


def counts_from_snowflake() -> dict[str, dict[str, int]]:
    """Read the two rows of mart_conversion_windows directly."""
    try:
        import snowflake.connector  # type: ignore
        from dotenv import load_dotenv  # type: ignore
        load_dotenv()
    except ImportError:
        sys.exit("Install deps for --from-snowflake: pip install -r requirements.txt")

    conn = snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ.get("SNOWFLAKE_PASSWORD"),
        role=os.environ.get("SNOWFLAKE_ROLE", "ROLE_BI_READER"),
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "WH_BI"),
        database=os.environ.get("SNOWFLAKE_DATABASE", "LIVE_COMMERCE"),
        schema="MARTS",
    )
    cur = conn.cursor()
    # mart_conversion_windows is now at state × date × channel × daypart grain, so we
    # re-aggregate to the two states (SUM the additive counts) for the population z-test.
    cur.execute(
        "select airtime_state, sum(orders) as orders, sum(web_sessions) as web_sessions "
        "from marts.mart_conversion_windows group by airtime_state"
    )
    out: dict[str, dict[str, int]] = {}
    for state, orders, sessions in cur.fetchall():
        out[state] = {"orders": int(orders), "sessions": int(sessions)}
    cur.close()
    conn.close()
    return out


def two_proportion_z_test(x1: int, n1: int, x2: int, n2: int) -> tuple[float, float, float, float]:
    """One-sided test that p1 > p2. Returns (p1, p2, z, p_value)."""
    p1 = x1 / n1
    p2 = x2 / n2
    # Pooled proportion under H0.
    p_pool = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
    if se == 0:
        return p1, p2, float("inf"), 0.0
    z = (p1 - p2) / se
    # One-sided p-value via the standard normal survival function.
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
    on = counts["on_air"]
    off = counts["off_air"]

    p_on, p_off, z, p_value = two_proportion_z_test(
        on["orders"], on["sessions"], off["orders"], off["sessions"]
    )

    lift_abs = p_on - p_off
    lift_rel = (p_on / p_off - 1) if p_off > 0 else float("inf")

    print("Two-proportion z-test — conversion during airtime vs off-air baseline")
    print("=" * 70)
    print(f"  on-air : {on['orders']:>8} orders / {on['sessions']:>10} sessions"
          f"  -> conversion {p_on:.5f}")
    print(f"  off-air: {off['orders']:>8} orders / {off['sessions']:>10} sessions"
          f"  -> conversion {p_off:.5f}")
    print("-" * 70)
    print(f"  absolute lift : {lift_abs:+.5f}  ({lift_abs * 1000:+.2f} per 1,000 sessions)")
    print(f"  relative lift : {lift_rel:+.1%}")
    print(f"  z-statistic   : {z:.3f}")
    print(f"  p-value       : {p_value:.3e}  (one-sided H1: p_on_air > p_off_air)")
    print("-" * 70)
    if p_value < args.alpha:
        print(f"  CONCLUSION: reject H0 at alpha={args.alpha}. The conversion rate during")
        print(f"  airtime is significantly higher than the off-air baseline. The airtime")
        print(f"  effect is statistically detectable, not noise.")
    else:
        print(f"  CONCLUSION: fail to reject H0 at alpha={args.alpha}. No significant lift.")
    print("=" * 70)


if __name__ == "__main__":
    main()
