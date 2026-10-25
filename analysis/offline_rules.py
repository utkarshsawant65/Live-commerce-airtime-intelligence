"""
Offline re-implementation of the dbt rules, run on a generated batch (CSV files).

Mirrors int_orders_attributed_to_airtime and mart_conversion_windows so the z-test and the
validation can run without Snowflake. Keep the two in sync if either changes.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import os
import sys
from dataclasses import dataclass, field

LAG_MINUTES = 30  # must match dbt var attribution_lag_minutes


def parse_ts(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)


def minute_of(s: str) -> int:
    return int(parse_ts(s).timestamp()) // 60


def read_csv(path: str) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@dataclass
class Batch:
    channels: list[dict]
    products: list[dict]
    presenters: list[dict]
    segments: list[dict]
    order_lines: list[dict]
    returns: list[dict]
    traffic: list[dict]
    manifest: dict = field(default_factory=dict)
    truth: dict[str, dict] = field(default_factory=dict)

    @property
    def online_channels(self) -> set[str]:
        return {c["channel_id"] for c in self.channels if c["channel_type"] == "online"}


def load_batch(in_dir: str, with_truth: bool = False) -> Batch:
    names = ["dim_channel", "dim_product", "dim_presenter", "fact_airtime_segment",
             "fact_order_line", "fact_returns", "fact_traffic"]
    paths = {n: os.path.join(in_dir, f"{n}.csv") for n in names}
    for p in paths.values():
        if not os.path.exists(p):
            sys.exit(f"Missing {p}. Run the generator first: python generator/generate_batch.py")
    manifest_path = os.path.join(in_dir, "_batch_manifest.json")
    manifest = {}
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    truth = {}
    truth_path = os.path.join(in_dir, "_truth_order_attribution.csv")
    if with_truth:
        if not os.path.exists(truth_path):
            sys.exit(f"Missing {truth_path}; regenerate the batch with the current generator.")
        truth = {t["order_line_id"]: t for t in read_csv(truth_path)}
    return Batch(
        channels=read_csv(paths["dim_channel"]),
        products=read_csv(paths["dim_product"]),
        presenters=read_csv(paths["dim_presenter"]),
        segments=read_csv(paths["fact_airtime_segment"]),
        order_lines=read_csv(paths["fact_order_line"]),
        returns=read_csv(paths["fact_returns"]),
        traffic=read_csv(paths["fact_traffic"]),
        manifest=manifest,
        truth=truth,
    )


def on_air_minutes(segments: list[dict], lag: int = LAG_MINUTES) -> set[int]:
    """Minutes inside any segment's [start, end + lag)."""
    minutes: set[int] = set()
    for s in segments:
        minutes.update(range(minute_of(s["start_ts_utc"]), minute_of(s["end_ts_utc"]) + lag))
    return minutes


def attribute(order_lines: list[dict], segments: list[dict], lag: int = LAG_MINUTES) -> dict[str, int]:
    """order_line_id -> attributed segment id (-1 = organic).

    Same product, order time in [start, end + lag), latest start wins, then highest id.
    """
    by_product: dict[str, list[tuple[dt.datetime, dt.datetime, int]]] = {}
    for s in segments:
        by_product.setdefault(s["product_id"], []).append(
            (parse_ts(s["start_ts_utc"]),
             parse_ts(s["end_ts_utc"]) + dt.timedelta(minutes=lag),
             int(s["segment_id"])))
    result = {}
    for o in order_lines:
        ts = parse_ts(o["event_ts_utc"])
        best = None
        for start, window_end, seg_id in by_product.get(o["product_id"], ()):
            if start <= ts < window_end and (best is None or (start, seg_id) > best[:2]):
                best = (start, seg_id)
        result[o["order_line_id"]] = best[1] if best else -1
    return result


def conversion_counts(batch: Batch) -> dict[str, dict[str, int]]:
    """On-air / off-air distinct online orders and sessions (mart_conversion_windows rule)."""
    online = batch.online_channels
    on_air = on_air_minutes(batch.segments)
    orders: dict[str, set[str]] = {"on_air": set(), "off_air": set()}
    for o in batch.order_lines:
        if o["channel_id"] in online:
            state = "on_air" if minute_of(o["event_ts_utc"]) in on_air else "off_air"
            orders[state].add(o["order_id"])
    sessions = {"on_air": 0, "off_air": 0}
    for t in batch.traffic:
        if t["channel_id"] in online:
            state = "on_air" if minute_of(t["minute_ts_utc"]) in on_air else "off_air"
            sessions[state] += int(t["web_sessions"])
    return {s: {"orders": len(orders[s]), "sessions": sessions[s]} for s in ("on_air", "off_air")}


def conversion_counts_by_day(batch: Batch) -> dict[str, dict[str, dict[str, int]]]:
    """Same counts split by UTC date, for a day-level bootstrap."""
    online = batch.online_channels
    on_air = on_air_minutes(batch.segments)
    days: dict[str, dict[str, dict]] = {}

    def bucket(day):
        return days.setdefault(day, {s: {"orders": set(), "sessions": 0} for s in ("on_air", "off_air")})

    for o in batch.order_lines:
        if o["channel_id"] in online:
            state = "on_air" if minute_of(o["event_ts_utc"]) in on_air else "off_air"
            bucket(o["event_ts_utc"][:10])[state]["orders"].add(o["order_id"])
    for t in batch.traffic:
        if t["channel_id"] in online:
            state = "on_air" if minute_of(t["minute_ts_utc"]) in on_air else "off_air"
            bucket(t["minute_ts_utc"][:10])[state]["sessions"] += int(t["web_sessions"])
    return {d: {s: {"orders": len(v[s]["orders"]), "sessions": v[s]["sessions"]} for s in v}
            for d, v in days.items()}
