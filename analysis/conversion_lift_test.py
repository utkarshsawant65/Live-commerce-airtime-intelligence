"""
One-sided two-proportion z-test: is online conversion (distinct orders / web sessions)
higher on air than off air?

Counts come from marts.mart_conversion_windows (--from-snowflake) or are recomputed from
the generated CSVs with the same rules (analysis/offline_rules.py). Sessions are treated as
independent trials; --bootstrap-days adds a day-level bootstrap CI that relaxes that.
On generated data this measures a planted effect; the manifest's expected lift is printed
for comparison.

Usage:
    python analysis/conversion_lift_test.py --in generator/output
    python analysis/conversion_lift_test.py --in generator/output --bootstrap-days 2000
    python analysis/conversion_lift_test.py --from-snowflake
"""

from __future__ import annotations

import argparse
import math
import os
import random
import sys
from statistics import NormalDist

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from offline_rules import conversion_counts, conversion_counts_by_day, load_batch  # noqa: E402

Counts = dict[str, dict[str, int]]


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


def lift_intervals(x1: int, n1: int, x2: int, n2: int, level: float = 0.95):
    """Wald CI for p1 - p2 and log-ratio CI for p1 / p2 - 1. Relative CI is None if a count is 0."""
    zq = NormalDist().inv_cdf(0.5 + level / 2)
    p1, p2 = x1 / n1, x2 / n2
    se_diff = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    diff = (p1 - p2 - zq * se_diff, p1 - p2 + zq * se_diff)
    if x1 == 0 or x2 == 0:
        return diff, None
    se_log = math.sqrt(1 / x1 - 1 / n1 + 1 / x2 - 1 / n2)
    log_rr = math.log(p1 / p2)
    rel = (math.exp(log_rr - zq * se_log) - 1, math.exp(log_rr + zq * se_log) - 1)
    return diff, rel


def bootstrap_days(by_day: dict, reps: int, seed: int = 0, level: float = 0.95):
    """Percentile CI of the relative lift, resampling whole days with replacement."""
    days = list(by_day.values())
    rnd = random.Random(seed)
    lifts = []
    for _ in range(reps):
        sample = [rnd.choice(days) for _ in days]
        tot = {s: {k: sum(d[s][k] for d in sample) for k in ("orders", "sessions")}
               for s in ("on_air", "off_air")}
        if min(tot["on_air"]["sessions"], tot["off_air"]["sessions"], tot["off_air"]["orders"]) == 0:
            continue
        lifts.append((tot["on_air"]["orders"] / tot["on_air"]["sessions"])
                     / (tot["off_air"]["orders"] / tot["off_air"]["sessions"]) - 1)
    lifts.sort()
    if len(lifts) < 20:
        return None
    lo = lifts[int((1 - level) / 2 * len(lifts))]
    hi = lifts[min(len(lifts) - 1, int((0.5 + level / 2) * len(lifts)))]
    return lo, hi


def main() -> None:
    ap = argparse.ArgumentParser(description="Two-proportion z-test for airtime conversion lift.")
    ap.add_argument("--in", dest="in_dir", default="generator/output")
    ap.add_argument("--from-snowflake", action="store_true",
                    help="read counts from marts.mart_conversion_windows instead of CSVs")
    ap.add_argument("--alpha", type=float, default=0.05, help="significance level")
    ap.add_argument("--bootstrap-days", type=int, default=0, metavar="REPS",
                    help="also report a day-level bootstrap CI with this many resamples")
    args = ap.parse_args()

    batch = None if args.from_snowflake else load_batch(args.in_dir)
    counts = counts_from_snowflake() if args.from_snowflake else conversion_counts(batch)
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
    level = 1 - args.alpha
    diff_ci, rel_ci = lift_intervals(on["orders"], on["sessions"], off["orders"], off["sessions"], level)
    lift_rel = (p_on / p_off - 1) if p_off > 0 else float("inf")
    print(f"  conversion    : on-air {p_on:.5%}, off-air {p_off:.5%}")
    print(f"  absolute lift : {(p_on - p_off) * 1000:+.3f} per 1,000 sessions "
          f"({level:.0%} CI {diff_ci[0] * 1000:+.3f} to {diff_ci[1] * 1000:+.3f})")
    rel_txt = f"({level:.0%} CI {rel_ci[0]:+.1%} to {rel_ci[1]:+.1%})" if rel_ci else "(CI undefined)"
    print(f"  relative lift : {lift_rel:+.1%} {rel_txt}")
    if args.bootstrap_days and batch is not None:
        boot = bootstrap_days(conversion_counts_by_day(batch), args.bootstrap_days, level=level)
        if boot:
            print(f"  day bootstrap : {level:.0%} CI {boot[0]:+.1%} to {boot[1]:+.1%} "
                  f"({len(conversion_counts_by_day(batch))} days)")
    print(f"  z-statistic   : {z:.3f}")
    print(f"  p-value       : {p_value:.3e}  (one-sided H1: p_on_air > p_off_air)")

    expected = (batch.manifest.get("planted", {}).get("expected", {}) if batch else {})
    planted = expected.get("expected_measured_lift")
    if planted is not None:
        inside = rel_ci is not None and rel_ci[0] <= planted <= rel_ci[1]
        print(f"  planted lift  : {planted:+.1%} (expected from generator parameters; "
              f"{'inside' if inside else 'outside'} the CI)")
    print("-" * 70)
    if p_value < args.alpha:
        print(f"  Reject H0 at alpha={args.alpha}: on-air conversion is higher than off-air.")
    else:
        print(f"  Fail to reject H0 at alpha={args.alpha}: no detectable lift.")
    print("=" * 70)


if __name__ == "__main__":
    main()
