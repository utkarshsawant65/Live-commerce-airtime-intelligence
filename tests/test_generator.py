import csv
import filecmp
import json

import generate_batch as gb
import load_to_snowflake
import pytest
from offline_rules import load_batch

FILES = ["dim_product.csv", "dim_presenter.csv", "fact_airtime_segment.csv", "fact_order_line.csv",
         "fact_returns.csv", "fact_traffic.csv", "fact_order_line.json", "_truth_order_attribution.csv"]


def _rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_same_seed_gives_identical_files(make_batch):
    a, _ = make_batch("a", seed=11)
    b, _ = make_batch("b", seed=11)
    for name in FILES:
        assert filecmp.cmp(a / name, b / name, shallow=False), name


def test_different_seed_changes_orders(make_batch):
    a, _ = make_batch("a", seed=11)
    b, _ = make_batch("b", seed=12)
    assert not filecmp.cmp(a / "fact_order_line.csv", b / "fact_order_line.csv", shallow=False)


def test_segments_never_overlap(make_batch):
    out, _ = make_batch(days=14)
    segs = sorted(_rows(out / "fact_airtime_segment.csv"), key=lambda s: s["start_ts_utc"])
    for prev, cur in zip(segs, segs[1:]):
        assert cur["start_ts_utc"] >= prev["end_ts_utc"]


def test_overlap_assertion_fires():
    segs = [{"channel_id": 1, "start_ts_utc": "2026-06-10 10:00:00", "end_ts_utc": "2026-06-10 10:10:00"},
            {"channel_id": 1, "start_ts_utc": "2026-06-10 10:05:00", "end_ts_utc": "2026-06-10 10:15:00"}]
    with pytest.raises(AssertionError):
        gb.assert_no_overlap(segs)


def test_shows_respect_categories(make_batch):
    out, _ = make_batch(days=7)
    category = {p["product_id"]: p["category"] for p in _rows(out / "dim_product.csv") if p["is_current"] == "True"}
    for s in _rows(out / "fact_airtime_segment.csv"):
        if s["show_id"] == "2":  # Jewellery Hour
            assert category[s["product_id"]] == "Jewellery"


def test_dirty_mode_only_touches_json(make_batch):
    clean, _ = make_batch("clean", seed=5)
    dirty, manifest = make_batch("dirty", seed=5, dirty=True)
    for name in FILES[:-2]:
        if name.endswith(".csv"):
            assert filecmp.cmp(clean / name, dirty / name, shallow=False), name
    types = manifest["dirty_data"]["types"]
    rows = json.loads((dirty / "fact_order_line.json").read_text())
    assert sum(r["product_id"] is None for r in rows) == types["null_product_id"]
    assert len(rows) == len(_rows(clean / "fact_order_line.csv")) + types["duplicate_order_lines"]


def test_some_orders_have_several_lines(make_batch):
    out, _ = make_batch()
    lines = _rows(out / "fact_order_line.csv")
    per_order = {}
    for o in lines:
        per_order[o["order_id"]] = per_order.get(o["order_id"], 0) + 1
    multi = sum(n > 1 for n in per_order.values()) / len(per_order)
    assert 0.10 < multi < 0.25
    assert max(per_order.values()) <= 3


def test_prices_follow_the_product_version_valid_at_order_time(make_batch):
    out, _ = make_batch(days=5)
    versions = {}
    for p in _rows(out / "dim_product.csv"):
        versions.setdefault(p["product_id"], []).append(p)
    changed = {pid for pid, vs in versions.items()
               if len(vs) == 2 and "2026-06-10" < vs[1]["valid_from"] < "2026-06-15"}
    assert changed, "expected some products to change inside the window"
    for o in _rows(out / "fact_order_line.csv"):
        if o["product_id"] in changed:
            day = o["event_ts_utc"][:10]
            v = next(v for v in versions[o["product_id"]] if v["valid_from"] <= day < v["valid_to"])
            assert float(o["unit_price_paid"]) <= float(v["list_price"]) + 0.01


def test_truth_covers_every_line_and_is_never_loaded(make_batch):
    out, _ = make_batch()
    batch = load_batch(str(out), with_truth=True)
    assert set(batch.truth) == {o["order_line_id"] for o in batch.order_lines}
    assert not any(t.startswith("_truth") for t in load_to_snowflake.RAW_TABLES)


def test_manifest_records_planted_parameters(make_batch):
    _, m = make_batch(onair_uplift=0.5, halo=2.0)
    planted = m["planted"]
    assert planted["onair_uplift"] == 0.5 and planted["halo"] == 2.0
    assert planted["expected"]["expected_measured_lift"] > 0
    _, m0 = make_batch("null", onair_uplift=0.0)
    assert m0["planted"]["expected"]["expected_measured_lift"] == pytest.approx(0.0)


def test_live_batches_continue_ids_and_keep_dimensions(tmp_path):
    import datetime as dt
    start = dt.datetime(2026, 6, 10, tzinfo=dt.timezone.utc)
    out = tmp_path / "live"
    m1 = gb.generate(str(out), start, 1, 42, 120, 10, 0, dim_seed=42, dim_start=start, dim_days=2)
    first_products = (out / "dim_product.csv").read_text()
    first_lines = {o["order_line_id"] for o in _rows(out / "fact_order_line.csv")}
    first_segments = {s["segment_id"] for s in _rows(out / "fact_airtime_segment.csv")}
    gb.generate(str(out), start + dt.timedelta(days=1), 1, 43, 120, 10, m1["max_order_id"],
                segment_seq_start=m1["max_segment_id"], dim_seed=42, dim_start=start, dim_days=2)
    assert (out / "dim_product.csv").read_text() == first_products
    assert not first_lines & {o["order_line_id"] for o in _rows(out / "fact_order_line.csv")}
    assert not first_segments & {s["segment_id"] for s in _rows(out / "fact_airtime_segment.csv")}
