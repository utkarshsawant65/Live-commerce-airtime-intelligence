"""Small, fast versions of the validation experiments (fixed seeds, so deterministic)."""

from statistics import mean

from offline_rules import attribute, conversion_counts, load_batch
from validate_against_truth import attribution_quality, null_test, recovery_table, seed_sweep


def test_null_effect_gives_no_lift():
    result = null_test(seeds=8, days=2)
    assert abs(result["mean_lift"]) < 0.05
    assert result["false_positive_rate"] <= 0.25


def test_planted_lift_is_recovered():
    result = seed_sweep(seeds=4, days=2)
    assert result["coverage"] >= 0.75
    assert abs(result["mean_measured"] - result["mean_planted"]) < 0.15


def test_recovery_table_mostly_passes(make_batch):
    out, _ = make_batch(days=5, seed=42)
    rows = recovery_table(load_batch(str(out), with_truth=True))
    passed = sum(r["pass"] == "pass" for r in rows)
    assert passed >= len(rows) - 2


def test_attribution_is_precise_but_misses_late_orders(make_batch):
    out, _ = make_batch(days=3)
    q = attribution_quality(load_batch(str(out), with_truth=True))
    assert q["precision"] > 0.9
    assert 0.7 < q["recall"] < 1.0


def test_longer_window_raises_recall(make_batch):
    out, _ = make_batch(days=3)
    batch = load_batch(str(out), with_truth=True)
    recalls = [attribution_quality(batch, w)["recall"] for w in (15, 30, 60)]
    assert recalls == sorted(recalls)


def test_attribution_rules():
    seg = {"segment_id": "1", "product_id": "9", "start_ts_utc": "2026-06-10 10:00:00",
           "end_ts_utc": "2026-06-10 10:10:00"}
    later = dict(seg, segment_id="2", start_ts_utc="2026-06-10 10:20:00", end_ts_utc="2026-06-10 10:25:00")
    lines = [
        {"order_line_id": "a", "product_id": "9", "event_ts_utc": "2026-06-10 10:00:00"},  # at start
        {"order_line_id": "b", "product_id": "9", "event_ts_utc": "2026-06-10 10:39:59"},  # inside tail
        {"order_line_id": "c", "product_id": "9", "event_ts_utc": "2026-06-10 11:00:00"},  # end of seg 2 tail
        {"order_line_id": "d", "product_id": "5", "event_ts_utc": "2026-06-10 10:05:00"},  # other product
        {"order_line_id": "e", "product_id": "9", "event_ts_utc": "2026-06-10 10:30:00"},  # both open
    ]
    got = attribute(lines, [seg, later])
    assert got == {"a": 1, "b": 2, "c": -1, "d": -1, "e": 2}


def test_offline_counts_use_online_channels_only(make_batch):
    out, _ = make_batch()
    batch = load_batch(str(out))
    counts = conversion_counts(batch)
    online_orders = {o["order_id"] for o in batch.order_lines if o["channel_id"] in batch.online_channels}
    assert counts["on_air"]["orders"] + counts["off_air"]["orders"] == len(online_orders)
    assert mean([counts["on_air"]["sessions"], counts["off_air"]["sessions"]]) > 0
