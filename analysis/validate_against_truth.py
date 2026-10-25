"""
Check what the pipeline measures against what the generator planted.

Per batch (--in): a parameter recovery table (planted vs measured with 95% CI), attribution
precision / recall against _truth_order_attribution.csv, and an attribution-window sweep.
With --experiments: a null-effect test, a seed sweep with CI coverage, and a halo check,
each over freshly generated batches.

The measurement side uses only the CSVs the pipeline sees (analysis/offline_rules.py
mirrors the dbt rules); the manifest and truth file are used only for the planted side.

Usage:
    python analysis/validate_against_truth.py --in generator/output
    python analysis/validate_against_truth.py --experiments --seeds 50 --markdown docs/VALIDATION.md
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import random
import sys
import tempfile
from statistics import NormalDist, mean, pstdev

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "generator"))

import generate_batch as gb  # noqa: E402
from conversion_lift_test import lift_intervals, two_proportion_z_test  # noqa: E402
from offline_rules import (Batch, attribute, conversion_counts, load_batch,  # noqa: E402
                           minute_of, parse_ts)

Z95 = NormalDist().inv_cdf(0.975)


# ---------------------------------------------------------------------------
# Per-batch recovery
# ---------------------------------------------------------------------------

def _row(name, planted, measured, ci, fmt="{:.3f}"):
    if ci is None or not math.isfinite(measured):
        return {"parameter": name, "planted": fmt.format(planted), "measured": "n/a",
                "ci": "n/a", "pass": "no data"}
    ok = ci[0] <= planted <= ci[1]
    return {"parameter": name, "planted": fmt.format(planted), "measured": fmt.format(measured),
            "ci": f"{fmt.format(ci[0])} to {fmt.format(ci[1])}", "pass": "pass" if ok else "FAIL"}


def _wilson(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + Z95 ** 2 / n
    c = (p + Z95 ** 2 / (2 * n)) / d
    h = Z95 * math.sqrt(p * (1 - p) / n + Z95 ** 2 / (4 * n * n)) / d
    return c - h, c + h


def _segment_exposure(batch: Batch, attributed: dict[str, int]):
    """Per segment: online attributed lines, online sessions while airing, tier, prime, cluster."""
    online = batch.online_channels
    sessions_by_minute: dict[int, int] = {}
    for t in batch.traffic:
        if t["channel_id"] in online:
            m = minute_of(t["minute_ts_utc"])
            sessions_by_minute[m] = sessions_by_minute.get(m, 0) + int(t["web_sessions"])
    hits: dict[int, int] = {}
    for o in batch.order_lines:
        seg = attributed[o["order_line_id"]]
        if seg != -1 and o["channel_id"] in online:
            hits[seg] = hits.get(seg, 0) + 1
    tier = {p["presenter_id"]: p["experience_tier"] for p in batch.presenters}
    prime_hours = set(batch.manifest.get("planted", {}).get("prime_hours_utc", range(18, 23)))
    rows = []
    for s in batch.segments:
        a, b = minute_of(s["start_ts_utc"]), minute_of(s["end_ts_utc"])
        rows.append({
            "hits": hits.get(int(s["segment_id"]), 0),
            "exposure": sum(sessions_by_minute.get(m, 0) for m in range(a, b)),
            "tier": tier[s["presenter_id"]],
            "prime": parse_ts(s["start_ts_utc"]).hour in prime_hours,
            "cluster": (s["start_ts_utc"][:10], s["show_id"]),
        })
    return rows


def _ratio(rows, num, den):
    def rate(sel):
        e = sum(r["exposure"] for r in rows if sel(r))
        return sum(r["hits"] for r in rows if sel(r)) / e if e else float("nan")
    d = rate(den)
    return rate(num) / d if d else float("nan")


def _cluster_bootstrap(rows, num, den, reps=1000, seed=0):
    clusters: dict = {}
    for r in rows:
        clusters.setdefault(r["cluster"], []).append(r)
    keys = list(clusters)
    rnd = random.Random(seed)
    vals = []
    for _ in range(reps):
        sample = [r for k in (rnd.choice(keys) for _ in keys) for r in clusters[k]]
        v = _ratio(sample, num, den)
        if math.isfinite(v):
            vals.append(v)
    vals.sort()
    if len(vals) < 50:
        return None
    return vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1]


def recovery_table(batch: Batch) -> list[dict]:
    planted = batch.manifest.get("planted")
    if not planted:
        sys.exit("Manifest has no 'planted' block; regenerate the batch with the current generator.")
    rows = []

    # 1. On-air conversion lift.
    c = conversion_counts(batch)
    on, off = c["on_air"], c["off_air"]
    _, rel_ci = lift_intervals(on["orders"], on["sessions"], off["orders"], off["sessions"])
    measured = (on["orders"] / on["sessions"]) / (off["orders"] / off["sessions"]) - 1
    rows.append(_row("on-air conversion lift", planted["expected"]["expected_measured_lift"],
                     measured, rel_ci, "{:+.1%}"))

    # 2-3. Segment weights, measured as online attributed orders per exposed session.
    attributed = attribute(batch.order_lines, batch.segments)
    seg = _segment_exposure(batch, attributed)
    tiers = planted["tier_multipliers"]
    for t in ("star", "rookie"):
        num, den = (lambda r, t=t: r["tier"] == t), (lambda r: r["tier"] == "established")
        rows.append(_row(f"tier ratio {t} / established", tiers[t] / tiers["established"],
                         _ratio(seg, num, den), _cluster_bootstrap(seg, num, den)))
    num, den = (lambda r: r["prime"]), (lambda r: not r["prime"])
    rows.append(_row("prime / non-prime segment weight", planted["prime_segment_mult"],
                     _ratio(seg, num, den), _cluster_bootstrap(seg, num, den)))

    # 4. Channel order mix.
    names = {ch["channel_id"]: ch["channel_name"] for ch in batch.channels}
    orders_by_channel: dict[str, set] = {}
    for o in batch.order_lines:
        orders_by_channel.setdefault(o["channel_id"], set()).add(o["order_id"])
    n = sum(len(v) for v in orders_by_channel.values())
    for ch, share in planted["expected"]["channel_order_share"].items():
        k = len(orders_by_channel.get(ch, ()))
        p = k / n if n else 0
        h = Z95 * math.sqrt(p * (1 - p) / n) if n else 1
        rows.append(_row(f"order share {names.get(ch, ch)}", share, p, (p - h, p + h), "{:.1%}"))

    # 5. Line-level return incidence by category, expected from the planted rates.
    versions: dict[str, list[dict]] = {}
    for p in batch.products:
        versions.setdefault(p["product_id"], []).append(p)
    rates, mult = planted["category_return_rates"], planted["premium_return_mult"]
    returned = {r["order_line_id"] for r in batch.returns}
    by_cat: dict[str, list[float]] = {}
    hits: dict[str, int] = {}
    for o in batch.order_lines:
        day = o["event_ts_utc"][:10]
        v = next((v for v in versions[o["product_id"]] if v["valid_from"] <= day < v["valid_to"]),
                 versions[o["product_id"]][-1])
        prob = rates.get(v["category"], 0.1) * (mult if v["list_price_band"] in ("premium", "luxury") else 1)
        by_cat.setdefault(v["category"], []).append(prob)
        hits[v["category"]] = hits.get(v["category"], 0) + (o["order_line_id"] in returned)
    for cat in sorted(by_cat):
        n_c = len(by_cat[cat])
        rows.append(_row(f"return rate {cat}", mean(by_cat[cat]), hits[cat] / n_c,
                         _wilson(hits[cat], n_c), "{:.1%}"))
    return rows


def attribution_quality(batch: Batch, lag: int = gb.LAG_MINUTES) -> dict:
    pred = attribute(batch.order_lines, batch.segments, lag)
    tp = fp = fn = 0
    for line_id, p in pred.items():
        t = int(batch.truth[line_id]["true_segment_id"])
        if p != -1 and p == t:
            tp += 1
        else:
            fp += p != -1
            fn += t != -1
    return {"window_min": lag, "precision": tp / (tp + fp) if tp + fp else float("nan"),
            "recall": tp / (tp + fn) if tp + fn else float("nan"),
            "attributed_lines": tp + fp, "true_airtime_lines": tp + fn}


# ---------------------------------------------------------------------------
# Multi-batch experiments
# ---------------------------------------------------------------------------

def _run(seed: int, days: int, **kwargs) -> tuple[dict, dict]:
    with tempfile.TemporaryDirectory() as tmp:
        manifest = gb.generate(tmp, dt.datetime(2026, 6, 10, tzinfo=dt.timezone.utc), days, seed,
                               120, 10, 0, **kwargs)
        counts = conversion_counts(load_batch(tmp))
    return manifest, counts


def _lift_stats(counts):
    on, off = counts["on_air"], counts["off_air"]
    res = two_proportion_z_test(on["orders"], on["sessions"], off["orders"], off["sessions"])
    _, rel_ci = lift_intervals(on["orders"], on["sessions"], off["orders"], off["sessions"])
    lift = (on["orders"] / on["sessions"]) / (off["orders"] / off["sessions"]) - 1
    return lift, rel_ci, (res[3] if res else 1.0)


def null_test(seeds: int, days: int, alpha: float = 0.05) -> dict:
    lifts, sig = [], 0
    for s in range(1, seeds + 1):
        _, counts = _run(s, days, onair_uplift=0.0)
        lift, _, p = _lift_stats(counts)
        lifts.append(lift)
        sig += p < alpha
    return {"seeds": seeds, "days": days, "false_positive_rate": sig / seeds,
            "mean_lift": mean(lifts), "sd_lift": pstdev(lifts)}


def seed_sweep(seeds: int, days: int) -> dict:
    lifts, planted, covered = [], [], 0
    for s in range(1, seeds + 1):
        m, counts = _run(s, days)
        lift, ci, _ = _lift_stats(counts)
        exp = m["planted"]["expected"]["expected_measured_lift"]
        lifts.append(lift)
        planted.append(exp)
        covered += ci is not None and ci[0] <= exp <= ci[1]
    return {"seeds": seeds, "days": days, "mean_planted": mean(planted), "mean_measured": mean(lifts),
            "sd_measured": pstdev(lifts), "min_measured": min(lifts), "max_measured": max(lifts),
            "coverage": covered / seeds}


def halo_check(halos: list[float], seeds: int, days: int) -> list[dict]:
    out = []
    for h in halos:
        lifts, planted = [], []
        for s in range(1, seeds + 1):
            m, counts = _run(s, days, halo=h)
            lifts.append(_lift_stats(counts)[0])
            planted.append(m["planted"]["expected"]["expected_measured_lift"])
        out.append({"halo": h, "mean_planted": mean(planted), "mean_measured": mean(lifts),
                    "sd_measured": pstdev(lifts)})
    return out


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _md_table(rows: list[dict], cols: list[str]) -> str:
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(str(r[c]) for c in cols) + " |" for r in rows]
    return "\n".join(lines)


def _print_table(rows: list[dict], cols: list[str]) -> None:
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
    print("  ".join(c.ljust(widths[c]) for c in cols))
    for r in rows:
        print("  ".join(str(r[c]).ljust(widths[c]) for c in cols))


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare measured results with planted parameters.")
    ap.add_argument("--in", dest="in_dir", default="generator/output")
    ap.add_argument("--experiments", action="store_true",
                    help="also run the null test, seed sweep and halo check")
    ap.add_argument("--seeds", type=int, default=50)
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--halo-seeds", type=int, default=10)
    ap.add_argument("--markdown", metavar="PATH", help="write the results as Markdown")
    args = ap.parse_args()

    batch = load_batch(args.in_dir, with_truth=True)
    md = ["# Validation", "",
          "Generated by `analysis/validate_against_truth.py`. The pipeline side uses only the "
          "CSVs that are loaded into Snowflake; the planted side comes from the generator "
          "manifest and `_truth_order_attribution.csv`.", ""]
    m = batch.manifest
    md += [f"Batch: `--days {m['days']} --seed {m['seed']}` "
           f"({m['counts']['orders']:,} orders, {m['counts']['airtime_segments']} segments).", ""]

    rec = recovery_table(batch)
    print("Parameter recovery (95% CI)")
    _print_table(rec, ["parameter", "planted", "measured", "ci", "pass"])
    md += ["## Parameter recovery (95% CI)", "", _md_table(rec, ["parameter", "planted", "measured", "ci", "pass"]), "",
           "Lift CI: log ratio of the two proportions. Tier and prime ratios: online attributed "
           "orders per session watched during the segment, CI by bootstrap over shows. "
           "Shares and return rates: normal / Wilson intervals. "
           f"With {len(rec)} independent 95% intervals, about {0.05 * len(rec):.1f} misses are "
           f"expected by chance; this batch has {sum(r['pass'] == 'FAIL' for r in rec)}.", ""]

    windows = [attribution_quality(batch, w) for w in (15, 30, 45, 60, 90)]
    for w in windows:
        w["precision"], w["recall"] = f"{w['precision']:.1%}", f"{w['recall']:.1%}"
    print("\nAttribution vs truth (window = minutes after segment end)")
    _print_table(windows, ["window_min", "precision", "recall", "attributed_lines", "true_airtime_lines"])
    md += ["## Attribution against ground truth", "",
           _md_table(windows, ["window_min", "precision", "recall", "attributed_lines", "true_airtime_lines"]),
           "", "The pipeline uses 30 minutes. Longer windows catch more late orders but also attribute "
           "more organic orders of the same product.", ""]

    if args.experiments:
        print(f"\nRunning experiments ({args.seeds} seeds x {args.days} days) ...")
        null = null_test(args.seeds, args.days)
        sweep = seed_sweep(args.seeds, args.days)
        halos = halo_check([1.0, 1.5, 2.0, 2.9, 4.0, 5.0], args.halo_seeds, args.days)
        print(f"Null effect (--onair-uplift 0): false-positive rate {null['false_positive_rate']:.0%} "
              f"at alpha 0.05, mean lift {null['mean_lift']:+.1%} (sd {null['sd_lift']:.1%})")
        print(f"Seed sweep: planted {sweep['mean_planted']:+.1%}, measured {sweep['mean_measured']:+.1%} "
              f"(sd {sweep['sd_measured']:.1%}, range {sweep['min_measured']:+.1%} to "
              f"{sweep['max_measured']:+.1%}), CI coverage {sweep['coverage']:.0%}")
        for h in halos:
            print(f"  halo {h['halo']:.1f}: planted {h['mean_planted']:+.1%}, measured {h['mean_measured']:+.1%}")
        md += ["## Null effect", "",
               f"{null['seeds']} batches of {null['days']} days with `--onair-uplift 0`: "
               f"{null['false_positive_rate']:.0%} significant at alpha = 0.05 (expected about 5%); "
               f"mean measured lift {null['mean_lift']:+.1%} (sd {null['sd_lift']:.1%}).", "",
               "## Seed sweep", "",
               f"{sweep['seeds']} batches of {sweep['days']} days, default parameters: planted lift "
               f"{sweep['mean_planted']:+.1%} on average, measured {sweep['mean_measured']:+.1%} "
               f"(sd {sweep['sd_measured']:.1%}, range {sweep['min_measured']:+.1%} to "
               f"{sweep['max_measured']:+.1%}). The 95% CI contained the planted lift in "
               f"{sweep['coverage']:.0%} of batches.", "",
               "## Halo check", "",
               "The halo multiplies on-air traffic. Orders follow sessions, so the measured "
               f"conversion lift should not depend on it ({args.halo_seeds} seeds per value):", "",
               _md_table([{"halo": f"{h['halo']:.1f}", "planted": f"{h['mean_planted']:+.1%}",
                           "measured": f"{h['mean_measured']:+.1%}", "sd": f"{h['sd_measured']:.1%}"}
                          for h in halos], ["halo", "planted", "measured", "sd"]), ""]

    if args.markdown:
        os.makedirs(os.path.dirname(os.path.abspath(args.markdown)), exist_ok=True)
        with open(args.markdown, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(md) + "\n")
        print(f"\nWrote {args.markdown}")


if __name__ == "__main__":
    main()
