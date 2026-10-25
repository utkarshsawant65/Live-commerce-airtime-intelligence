"""
Generate one batch of synthetic live-commerce data with known, planted parameters.

Online orders are driven by sessions: a baseline conversion everywhere, plus incremental
orders for the product on air. Every planted parameter goes into _batch_manifest.json
("planted"), and the true segment behind each order line goes into
_truth_order_attribution.csv, which is never loaded into Snowflake. The pipeline measures
the parameters without seeing either; analysis/validate_against_truth.py compares them.

Usage:
    python generator/generate_batch.py --out generator/output
    python generator/generate_batch.py --out generator/output --days 5 --seed 42 --onair-uplift 0
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone

import numpy as np

CHANNELS = [
    # channel_id, channel_name, channel_type
    (1, "TV Linear", "broadcast"),
    (2, "Web Shop", "online"),
    (3, "Mobile App", "online"),
    (4, "Call Centre", "phone"),
]
TV_CHANNEL_ID = 1

CATEGORIES = [
    # name, base_return_rate
    ("Fashion", 0.28),
    ("Jewellery", 0.24),
    ("Beauty", 0.10),
    ("Home & Living", 0.08),
    ("Electronics", 0.06),
    ("Wellness", 0.07),
]
PREMIUM_RETURN_MULT = 1.2  # premium and luxury lines return more often

RETURN_REASONS = ["size_fit", "not_as_described", "changed_mind", "damaged", "late_delivery"]
RETURN_REASON_PROBS = [0.4, 0.2, 0.25, 0.1, 0.05]

EXPERIENCE_TIERS = ["rookie", "established", "star"]
TIER_SHARE = [0.3, 0.5, 0.2]

# --- Conversion model (online channels) -------------------------------------------
BASE_CONVERSION = 0.015     # orders per web session, on or off air
ONAIR_UPLIFT = 0.63         # incremental orders per exposed session, relative to baseline
HALO = 2.9                  # online sessions multiplier while a segment window is open

# Segment weight = tier x prime x price factor. Scales airtime orders only, never baseline.
TIER_LIFT_MULTIPLIER = {"rookie": 1.05, "established": 1.25, "star": 1.6}
PRIME_SEGMENT_MULT = 1.6


def price_factor(list_price: float) -> float:
    """Cheaper products convert better on air."""
    return float(np.clip(2.0 - list_price / 400.0, 0.4, 1.8))


# --- Phone and TV orders (outside the conversion metric) --------------------------
PHONE_BASE_CONVERSION = 0.2            # orders per call-centre contact
PHONE_ORDERS_PER_SEGMENT_MIN = 0.20    # x segment weight
TV_CALLIN_ORDERS_PER_SEGMENT_MIN = 0.20

# --- Traffic -----------------------------------------------------------------------
# Mean per minute: viewers (TV), sessions (online), contacts (phone). Online and phone
# counts are Poisson; TV viewers are normal with this standard deviation.
BASELINE_TRAFFIC = {1: 4000, 2: 11.0, 3: 7.0, 4: 1.2}
TV_VIEWERS_STD = 1200
PRIME_TIME_HOURS = set(range(18, 23))  # 18:00-22:59 UTC


def hour_traffic_mult(hour: int) -> float:
    if hour < 7:
        return 0.3
    if hour in PRIME_TIME_HOURS:
        return 2.1
    if hour == 23:
        return 0.8
    return 1.0


# --- Response lag: minutes from segment start to order, lognormal with a long tail ---
LAG_MEDIAN_MIN = 9.0
LAG_SIGMA = 1.25
LAG_CAP_MIN = 360
LAG_MINUTES = 30  # attribution window tail; must match dbt var attribution_lag_minutes

# --- Baskets and product history -------------------------------------------------
ADDON_ORDER_SHARE = 0.17   # orders that get 1-2 extra lines for other products
PRE_WINDOW_CHANGE_SHARE = 0.25
IN_WINDOW_CHANGE_SHARE = 0.10

# --- Schedule: one TV channel, shows in fixed, non-overlapping slots ---------------
# name, format, earliest start hour, latest end hour, categories (None = any), days
SHOWS = [
    ("Morning Glow", "magazine", 8, 11, ["Beauty", "Wellness", "Home & Living"], "all"),
    ("Jewellery Hour", "themed", 13, 17, ["Jewellery"], "weekday"),
    ("Weekend Style Edit", "themed", 13, 17, ["Fashion", "Beauty"], "weekend"),
    ("Prime Time Live", "flagship", 18, 22, None, "all"),
    ("Late Night Deals", "clearance", 22, 24, None, "all"),
]
SHOW_PROBABILITY = {"Morning Glow": 0.8, "Jewellery Hour": 0.85,
                    "Weekend Style Edit": 0.9, "Prime Time Live": 1.0, "Late Night Deals": 0.7}


@dataclass
class Product:
    product_id: int
    product_version: int
    product_name: str
    category: str
    brand: str
    list_price: float
    list_price_band: str
    valid_from: str   # UTC date
    valid_to: str     # UTC date or '9999-12-31'
    is_current: bool


# ---------------------------------------------------------------------------
# Dimensions
# ---------------------------------------------------------------------------

def _band_for_price(price: float) -> str:
    if price < 30:
        return "budget"
    if price < 100:
        return "mid"
    if price < 300:
        return "premium"
    return "luxury"


# (adjectives, item nouns) per category, combined into names like "Cashmere Wrap Coat".
_NAME_PARTS = {
    "Fashion": (["Cashmere", "Merino", "Linen", "Silk", "Quilted", "Tailored", "Wrap", "Pleated"],
                ["Wrap Coat", "Knit Sweater", "Midi Dress", "Trench Coat", "Scarf", "Blazer", "Tunic", "Cardigan"]),
    "Jewellery": (["18k Gold", "Sterling Silver", "Diamond", "Pearl", "Rose Gold", "Sapphire", "Emerald", "Vintage"],
                  ["Hoop Earrings", "Tennis Bracelet", "Pendant Necklace", "Stud Earrings", "Eternity Ring", "Charm Bracelet", "Signet Ring", "Choker"]),
    "Beauty": (["Vitamin C", "Retinol", "Hyaluronic", "Collagen", "Radiance", "Hydra", "Age-Defy", "Glow"],
               ["Serum", "Day Cream", "Eye Cream", "Cleansing Foam", "Face Mask", "Night Repair", "Toner", "Lip Balm"]),
    "Home & Living": (["Nordic", "Velvet", "Oak", "Linen", "Handwoven", "Ceramic", "Bamboo", "Cotton"],
                      ["Throw Blanket", "Accent Chair", "Table Lamp", "Cushion Set", "Dinnerware Set", "Floor Rug", "Storage Basket", "Wall Mirror"]),
    "Electronics": (["Pro", "Smart", "Wireless", "Ultra", "Compact", "HD", "Turbo", "Eco"],
                    ["Air Purifier", "Robot Vacuum", "Sound Bar", "Steam Iron", "Blender", "Air Fryer", "Coffee Machine", "Smartwatch"]),
    "Wellness": (["Organic", "Active", "Daily", "Pure", "Vital", "Restore", "Calm", "Boost"],
                 ["Collagen Powder", "Magnesium Complex", "Omega-3 Caps", "Sleep Drops", "Probiotic", "Joint Support", "Multivitamin", "Protein Blend"]),
}


def _product_name(rng: np.random.Generator, category: str, pid: int) -> str:
    """Random "<adjective> <item>" name for the category (not unique across products)."""
    parts = _NAME_PARTS.get(category)
    if not parts:
        return f"Item {pid}"
    adj = parts[0][rng.integers(0, len(parts[0]))]
    noun = parts[1][rng.integers(0, len(parts[1]))]
    return f"{adj} {noun}"


def build_products(rng: np.random.Generator, n_products: int, window_start: datetime,
                   days: int) -> list[Product]:
    """Products with SCD2 history: some changed before the window, some change inside it."""
    brands = ["Auriva", "Lumino", "Casa Verde", "NordTech", "Belle Maison", "PureForm"]
    first_seen = (window_start - timedelta(days=120)).date().isoformat()
    products: list[Product] = []
    for pid in range(1, n_products + 1):
        category = CATEGORIES[rng.integers(0, len(CATEGORIES))][0]
        brand = brands[rng.integers(0, len(brands))]
        # Log-normal prices; parameters chosen so every price band is well populated.
        price = float(round(np.exp(rng.normal(4.3, 1.0)), 2))
        price = min(max(price, 7.99), 1999.0)
        name = _product_name(rng, category, pid)

        r = rng.random()
        if r < PRE_WINDOW_CHANGE_SHARE:
            # Earlier version differs by category or by a lower price; changed 30 days back.
            change = (window_start - timedelta(days=30)).date().isoformat()
            if rng.random() < 0.5:
                old_category, old_price = CATEGORIES[rng.integers(0, len(CATEGORIES))][0], price
            else:
                old_category, old_price = category, float(round(price * rng.uniform(0.55, 0.8), 2))
            versions = [(old_category, old_price), (category, price)]
        elif r < PRE_WINDOW_CHANGE_SHARE + IN_WINDOW_CHANGE_SHARE and days >= 2:
            # Price change on a day inside the window.
            change = (window_start + timedelta(days=int(rng.integers(1, days)))).date().isoformat()
            versions = [(category, price), (category, float(round(price * rng.uniform(0.7, 0.9), 2)))]
        else:
            change, versions = None, [(category, price)]

        bounds = [first_seen] + ([change] if change else []) + ["9999-12-31"]
        for v, (cat, p) in enumerate(versions, start=1):
            products.append(Product(
                product_id=pid, product_version=v, product_name=name, category=cat,
                brand=brand, list_price=p, list_price_band=_band_for_price(p),
                valid_from=bounds[v - 1], valid_to=bounds[v], is_current=(v == len(versions)),
            ))
    return products


def build_presenters(rng: np.random.Generator, n: int) -> list[dict]:
    first = ["Anja", "Markus", "Lena", "Tobias", "Sofia", "Jonas", "Clara", "Felix",
             "Nina", "David", "Mara", "Paul"]
    # Fixed tier mix (shuffled) so every tier is present even with few presenters.
    counts = [round(n * s) for s in TIER_SHARE]
    counts[1] += n - sum(counts)
    tiers = rng.permutation([t for t, c in zip(EXPERIENCE_TIERS, counts) for _ in range(c)])
    return [{"presenter_id": i,
             "presenter_name": f"{first[(i - 1) % len(first)]} P{i}",
             "experience_tier": str(tiers[i - 1])}
            for i in range(1, n + 1)]


def build_shows() -> list[dict]:
    return [{"show_id": i + 1, "show_name": s[0], "show_format": s[1]}
            for i, s in enumerate(SHOWS)]


# ---------------------------------------------------------------------------
# Schedule and traffic
# ---------------------------------------------------------------------------

def build_airtime_segments(rng, products, presenters, shows, window_start, days,
                           segment_seq_start=0) -> list[dict]:
    """Daily TV schedule. Show slots never overlap; each show is a run of 5-14 min segments
    for products from the show's categories, hosted by one presenter."""
    current = [p for p in products if p.is_current]
    by_category: dict[str, list[Product]] = {}
    for p in current:
        by_category.setdefault(p.category, []).append(p)

    segments: list[dict] = []
    seg_id = segment_seq_start
    for d in range(days):
        day = window_start + timedelta(days=d)
        weekend = day.weekday() >= 5
        for show, (name, _fmt, first_hour, last_hour, cats, when) in zip(shows, SHOWS):
            if (when == "weekday" and weekend) or (when == "weekend" and not weekend):
                continue
            if rng.random() > SHOW_PROBABILITY[name]:
                continue
            start_hour = int(rng.integers(first_hour, max(first_hour + 1, last_hour - 1)))
            slot_end = day.replace(hour=0, minute=0, second=0) + timedelta(hours=last_hour)
            cursor = day.replace(hour=start_hour, minute=0, second=0)
            show_minutes = min(int(rng.integers(60, 121)), int((slot_end - cursor).total_seconds() // 60))
            pool = [p for c in cats for p in by_category.get(c, [])] if cats else current
            pool = pool or current
            presenter = presenters[rng.integers(0, len(presenters))]
            elapsed = 0
            while elapsed < show_minutes - 5:
                dur = min(int(rng.integers(5, 15)), show_minutes - elapsed)
                product = pool[rng.integers(0, len(pool))]
                start = cursor + timedelta(minutes=elapsed)
                seg_id += 1
                segments.append({
                    "segment_id": seg_id,
                    "product_id": product.product_id,
                    "presenter_id": presenter["presenter_id"],
                    "show_id": show["show_id"],
                    "channel_id": TV_CHANNEL_ID,
                    "start_ts_utc": _iso(start),
                    "end_ts_utc": _iso(start + timedelta(minutes=dur)),
                    "segment_duration_minutes": dur,
                })
                elapsed += dur
    assert_no_overlap(segments)
    return segments


def assert_no_overlap(segments: list[dict]) -> None:
    """Segments on the same channel must not overlap in [start, end)."""
    by_channel: dict[int, list[tuple[str, str]]] = {}
    for s in segments:
        by_channel.setdefault(s["channel_id"], []).append((s["start_ts_utc"], s["end_ts_utc"]))
    for spans in by_channel.values():
        spans.sort()
        for (_, prev_end), (start, _) in zip(spans, spans[1:]):
            if start < prev_end:
                raise AssertionError(f"overlapping segments: {start} starts before {prev_end}")


def on_air_mask(segments: list[dict], window_start: datetime, total_minutes: int) -> np.ndarray:
    """True for window minutes inside any segment's [start, end + LAG_MINUTES)."""
    mask = np.zeros(total_minutes, dtype=bool)
    for s in segments:
        a = _minute_index(s["start_ts_utc"], window_start)
        b = _minute_index(s["end_ts_utc"], window_start) + LAG_MINUTES
        mask[max(a, 0):min(b, total_minutes)] = True
    return mask


def build_traffic(rng, mask: np.ndarray, window_start: datetime, halo: float) -> dict[int, np.ndarray]:
    """Per-minute traffic per channel: viewers (TV), sessions (online), contacts (phone)."""
    n = len(mask)
    hours = np.array([(window_start + timedelta(minutes=i)).hour for i in range(n)])
    hour_mult = np.array([hour_traffic_mult(h) for h in range(24)])[hours]
    traffic = {}
    for ch_id, _name, ch_type in CHANNELS:
        mu = BASELINE_TRAFFIC[ch_id] * hour_mult * (np.where(mask, halo, 1.0) if ch_type == "online" else 1.0)
        if ch_type == "broadcast":
            traffic[ch_id] = np.maximum(0, rng.normal(mu, TV_VIEWERS_STD * hour_mult)).astype(np.int64)
        else:
            traffic[ch_id] = rng.poisson(mu).astype(np.int64)
    return traffic


# ---------------------------------------------------------------------------
# Orders and returns
# ---------------------------------------------------------------------------

def _lag_cdf(x: float) -> float:
    if x <= 0:
        return 0.0
    return 0.5 * (1 + math.erf((math.log(x) - math.log(LAG_MEDIAN_MIN)) / (LAG_SIGMA * math.sqrt(2))))


def _lag_minute_probs() -> np.ndarray:
    """P(order lands j whole minutes after segment start), j = 0..LAG_CAP_MIN (cap absorbs the tail)."""
    cdf = np.array([_lag_cdf(j) for j in range(LAG_CAP_MIN + 1)])
    probs = np.diff(cdf, append=1.0)
    return probs


class _Ids:
    def __init__(self, order_seq_start: int):
        self.order = order_seq_start
        self.line = order_seq_start * 4   # at most 3 lines per order
        self.ret = order_seq_start * 2


def build_orders_and_returns(rng, products, presenters, segments, traffic, window_start,
                             order_seq_start, base_conversion, onair_uplift):
    """Returns (order_lines, returns, truth, expected) where expected holds the planted
    expectations the validation compares against."""
    n_min = len(next(iter(traffic.values())))
    window_end = window_start + timedelta(minutes=n_min)
    presenter_tier = {p["presenter_id"]: p["experience_tier"] for p in presenters}
    versions: dict[int, list[Product]] = {}
    for p in products:
        versions.setdefault(p.product_id, []).append(p)
    current = [v[-1] for v in versions.values()]
    online = [c[0] for c in CHANNELS if c[2] == "online"]
    phone = [c[0] for c in CHANNELS if c[2] == "phone"]
    ids = _Ids(order_seq_start)
    order_lines, returns, truth = [], [], []
    exp_orders = {c[0]: 0.0 for c in CHANNELS}

    def version_at(pid: int, ts: datetime) -> Product:
        day = ts.date().isoformat()
        for v in versions[pid]:
            if v.valid_from <= day < v.valid_to:
                return v
        return versions[pid][-1]

    def add_order(pid, channel_id, ts, source, segment_id):
        ids.order += 1
        lines = [pid]
        if rng.random() < ADDON_ORDER_SHARE:
            for _ in range(int(rng.integers(1, 3))):
                lines.append(current[rng.integers(0, len(current))].product_id)
        for k, line_pid in enumerate(lines):
            ids.line += 1
            product = version_at(line_pid, ts)
            ol = _make_order_line(rng, ids.line, ids.order, product, channel_id, ts)
            order_lines.append(ol)
            truth.append({"order_line_id": ids.line,
                          "true_segment_id": segment_id if k == 0 else -1,
                          "order_source": source if k == 0 else "addon"})
            _maybe_return(rng, ol, product, returns, ids)

    def minute_ts(i: int) -> datetime:
        return window_start + timedelta(minutes=int(i), seconds=int(rng.integers(0, 60)))

    # Baseline: every online and phone minute converts at the base rate, product at random.
    for ch in online + phone:
        rate = base_conversion if ch in online else PHONE_BASE_CONVERSION
        lam = traffic[ch] * rate
        exp_orders[ch] += float(lam.sum())
        for i in np.nonzero(counts := rng.poisson(lam))[0]:
            for _ in range(int(counts[i])):
                add_order(current[rng.integers(0, len(current))].product_id, ch,
                          minute_ts(i), "baseline", -1)

    # Airtime: incremental orders per segment, timed by the response lag.
    lag_probs = _lag_minute_probs()
    for seg in segments:
        st = _parse(seg["start_ts_utc"])
        a = _minute_index(seg["start_ts_utc"], window_start)
        b = a + seg["segment_duration_minutes"]
        product = version_at(seg["product_id"], st)
        weight = (TIER_LIFT_MULTIPLIER[presenter_tier[seg["presenter_id"]]]
                  * (PRIME_SEGMENT_MULT if st.hour in PRIME_TIME_HOURS else 1.0)
                  * price_factor(product.list_price))
        lam = {ch: base_conversion * onair_uplift * weight * float(traffic[ch][a:b].sum())
               for ch in online}
        lam[phone[0]] = PHONE_ORDERS_PER_SEGMENT_MIN * seg["segment_duration_minutes"] * weight
        lam[TV_CHANNEL_ID] = TV_CALLIN_ORDERS_PER_SEGMENT_MIN * seg["segment_duration_minutes"] * weight
        # Only orders landing inside the window are generated; count the same in expectations.
        in_window = float(lag_probs[: max(0, min(len(lag_probs), n_min - a))].sum())
        for ch, l in lam.items():
            exp_orders[ch] += l * in_window
            for _ in range(int(rng.poisson(l))):
                ts = st + timedelta(minutes=float(min(rng.lognormal(math.log(LAG_MEDIAN_MIN), LAG_SIGMA),
                                                      LAG_CAP_MIN)))
                if ts < window_end:
                    add_order(seg["product_id"], ch, ts, "airtime", seg["segment_id"])

    expected = _expected_lift(segments, traffic, window_start, online, presenter_tier,
                              versions, base_conversion, onair_uplift, lag_probs)
    expected["orders_by_channel"] = exp_orders
    return order_lines, returns, truth, expected


def _expected_lift(segments, traffic, window_start, online, presenter_tier, versions,
                   base_conversion, onair_uplift, lag_probs) -> dict:
    """Exact expected on-air / off-air online orders given this schedule and traffic."""
    n_min = len(traffic[online[0]])
    mask = on_air_mask(segments, window_start, n_min)
    sessions = sum(traffic[ch].astype(float) for ch in online)
    s_on, s_off = float(sessions[mask].sum()), float(sessions[~mask].sum())
    inc_on = inc_off = 0.0
    for seg in segments:
        st = _parse(seg["start_ts_utc"])
        a = _minute_index(seg["start_ts_utc"], window_start)
        b = a + seg["segment_duration_minutes"]
        product = next(v for v in versions[seg["product_id"]]
                       if v.valid_from <= st.date().isoformat() < v.valid_to)
        weight = (TIER_LIFT_MULTIPLIER[presenter_tier[seg["presenter_id"]]]
                  * (PRIME_SEGMENT_MULT if st.hour in PRIME_TIME_HOURS else 1.0)
                  * price_factor(product.list_price))
        lam = base_conversion * onair_uplift * weight * float(sessions[a:b].sum())
        idx = a + np.arange(len(lag_probs))
        keep = idx < n_min
        inc_on += lam * float(lag_probs[keep][mask[idx[keep]]].sum())
        inc_off += lam * float(lag_probs[keep][~mask[idx[keep]]].sum())
    b_on, b_off = base_conversion * s_on, base_conversion * s_off
    rate_on = (b_on + inc_on) / s_on if s_on else float("nan")
    rate_off = (b_off + inc_off) / s_off if s_off else float("nan")
    return {
        "expected_measured_lift": rate_on / rate_off - 1 if s_on and s_off else None,
        "lift_on_air_minutes": inc_on / b_on if b_on else None,
        "expected_onair_orders": b_on + inc_on,
        "expected_offair_orders": b_off + inc_off,
        "incremental_share_outside_window": inc_off / (inc_on + inc_off) if inc_on + inc_off else None,
    }


def _make_order_line(rng, line_id, order_id, product: Product, channel_id, ts) -> dict:
    qty = int(rng.choice([1, 2, 3], p=[0.75, 0.2, 0.05]))
    unit_price_paid = round(product.list_price * rng.uniform(0.7, 1.0), 2)
    gross = round(unit_price_paid * qty, 2)
    discount = round(gross * rng.uniform(0.0, 0.15), 2)
    return {
        "order_line_id": line_id,
        "order_id": order_id,
        "product_id": product.product_id,
        "channel_id": channel_id,
        "event_ts_utc": _iso(ts),
        "quantity": qty,
        "unit_price_paid": unit_price_paid,
        "gross_amount": gross,
        "discount_amount": discount,
        "net_amount": round(gross - discount, 2),
    }


def return_probability(product: Product) -> float:
    rate = dict(CATEGORIES).get(product.category, 0.1)
    if product.list_price_band in ("premium", "luxury"):
        rate *= PREMIUM_RETURN_MULT
    return rate


def _maybe_return(rng, ol: dict, product: Product, returns: list, ids: _Ids) -> None:
    if rng.random() >= return_probability(product):
        return
    ids.ret += 1
    returned_qty = int(rng.integers(1, ol["quantity"] + 1))
    returns.append({
        "return_id": ids.ret,
        "order_line_id": ol["order_line_id"],
        "returned_qty": returned_qty,
        "refund_amount": round(ol["net_amount"] * returned_qty / ol["quantity"], 2),
        "return_ts_utc": _iso(_parse(ol["event_ts_utc"]) + timedelta(days=float(rng.uniform(1, 21)))),
        "reason_code": str(rng.choice(RETURN_REASONS, p=RETURN_REASON_PROBS)),
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _parse(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _minute_index(ts: str, window_start: datetime) -> int:
    return int((_parse(ts) - window_start).total_seconds() // 60)


def _write_csv(path: str, rows: list[dict], fieldnames: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------------------
# Dirty-data injection
# ---------------------------------------------------------------------------

def inject_dirt(rng: np.random.Generator, order_lines: list[dict],
                n_products: int, dirty_rate: float) -> dict:
    """Corrupt the JSON order-line feed in place and return a manifest of what was injected.

    Each corruption hits its own disjoint set of rows, so the counts are exact:
      duplicate_order_lines    exact copies appended    -> quarantine (duplicate_order_line)
      null_product_id          product_id = null        -> quarantine
      bad_quantity_type        quantity = "N/A", ...    -> try_cast + quarantine
      orphan_product_id        unknown product_id       -> quarantine (unknown_product_id)
      out_of_order_timestamps  event time 2-10 days earlier; well-formed, so stays valid
    """
    if not order_lines:
        return {"injected": False}

    n = len(order_lines)
    k = max(1, int(n * dirty_rate))
    kinds = ["duplicate_order_lines", "null_product_id", "bad_quantity_type",
             "orphan_product_id", "out_of_order_timestamps"]
    k = min(k, n // len(kinds))
    picked = rng.permutation(n)[:k * len(kinds)]
    idx = {kind: picked[j * k:(j + 1) * k] for j, kind in enumerate(kinds)}

    dupes = [dict(order_lines[i]) for i in idx["duplicate_order_lines"]]
    for i in idx["null_product_id"]:
        order_lines[i]["product_id"] = None
    for i in idx["bad_quantity_type"]:
        order_lines[i]["quantity"] = str(rng.choice(["N/A", "two", "-", "??"]))
    for i in idx["orphan_product_id"]:
        order_lines[i]["product_id"] = n_products + 9999
    for i in idx["out_of_order_timestamps"]:
        ts = _parse(order_lines[i]["event_ts_utc"])
        order_lines[i]["event_ts_utc"] = _iso(ts - timedelta(days=float(rng.uniform(2, 10))))
    order_lines.extend(dupes)

    return {
        "injected": True,
        "dirty_rate": dirty_rate,
        "feed": "fact_order_line.json",
        "json_rows": len(order_lines),
        "types": {kind: int(len(rows)) for kind, rows in idx.items()},
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def planted_parameters(base_conversion: float, onair_uplift: float, halo: float) -> dict:
    return {
        "base_conversion": base_conversion,
        "onair_uplift": onair_uplift,
        "halo": halo,
        "tier_multipliers": TIER_LIFT_MULTIPLIER,
        "prime_segment_mult": PRIME_SEGMENT_MULT,
        "prime_hours_utc": sorted(PRIME_TIME_HOURS),
        "price_factor": "clip(2 - list_price / 400, 0.4, 1.8)",
        "phone_base_conversion": PHONE_BASE_CONVERSION,
        "phone_orders_per_segment_min": PHONE_ORDERS_PER_SEGMENT_MIN,
        "tv_callin_orders_per_segment_min": TV_CALLIN_ORDERS_PER_SEGMENT_MIN,
        "baseline_traffic_mean": {str(k): v for k, v in BASELINE_TRAFFIC.items()},
        "category_return_rates": dict(CATEGORIES),
        "premium_return_mult": PREMIUM_RETURN_MULT,
        "return_reason_probs": dict(zip(RETURN_REASONS, RETURN_REASON_PROBS)),
        "lag": {"distribution": "lognormal", "median_min": LAG_MEDIAN_MIN,
                "sigma": LAG_SIGMA, "cap_min": LAG_CAP_MIN,
                "share_within_30_after_10_min_segment": round(_lag_cdf(40), 3)},
        "attribution_window_tail_min": LAG_MINUTES,
        "addon_order_share": ADDON_ORDER_SHARE,
    }


def generate(out_dir: str, start: datetime, days: int, seed: int,
             n_products: int, n_presenters: int, order_seq_start: int,
             dirty: bool = False, dirty_rate: float = 0.02,
             segment_seq_start: int = 0, dim_seed: int | None = None,
             dim_start: datetime | None = None, dim_days: int | None = None,
             base_conversion: float = BASE_CONVERSION, onair_uplift: float = ONAIR_UPLIFT,
             halo: float = HALO) -> dict:
    """Generate one batch into out_dir and return its manifest.

    dim_seed / dim_start / dim_days fix the dimension tables independently of the fact seed
    and window, so successive live batches describe the same products and presenters.
    """
    os.makedirs(out_dir, exist_ok=True)
    if dim_seed is None:
        dim_seed = seed
    dim_rng = np.random.default_rng(dim_seed)
    products = build_products(dim_rng, n_products, dim_start or start, dim_days or days)
    presenters = build_presenters(dim_rng, n_presenters)
    shows = build_shows()
    # With a single seed, keep one RNG stream so standalone output stays reproducible.
    rng = dim_rng if dim_seed == seed else np.random.default_rng(seed)

    total_minutes = days * 24 * 60
    segments = build_airtime_segments(rng, products, presenters, shows, start, days,
                                      segment_seq_start)
    mask = on_air_mask(segments, start, total_minutes)
    traffic = build_traffic(rng, mask, start, halo)
    order_lines, returns, truth, expected = build_orders_and_returns(
        rng, products, presenters, segments, traffic, start, order_seq_start,
        base_conversion, onair_uplift,
    )
    order_lines.sort(key=lambda o: o["order_line_id"])

    # Dirt goes only into the JSON feed (landed as VARIANT); the typed CSVs stay clean
    # so a strict COPY INTO never aborts on them.
    json_order_lines = order_lines
    dirt_manifest = {"injected": False}
    if dirty:
        json_order_lines = copy.deepcopy(order_lines)
        dirt_manifest = inject_dirt(rng, json_order_lines, n_products, dirty_rate)

    channels = [{"channel_id": c, "channel_name": n, "channel_type": t} for c, n, t in CHANNELS]
    traffic_rows = [
        {"channel_id": ch, "minute_ts_utc": _iso(start + timedelta(minutes=i)),
         "concurrent_viewers": int(v) if ch == TV_CHANNEL_ID else 0,
         "web_sessions": 0 if ch == TV_CHANNEL_ID else int(v)}
        for ch, series in traffic.items() for i, v in enumerate(series)
    ]

    _write_csv(os.path.join(out_dir, "dim_product.csv"),
               [asdict(p) for p in products],
               ["product_id", "product_version", "product_name", "category", "brand",
                "list_price", "list_price_band", "valid_from", "valid_to", "is_current"])
    _write_csv(os.path.join(out_dir, "dim_presenter.csv"), presenters,
               ["presenter_id", "presenter_name", "experience_tier"])
    _write_csv(os.path.join(out_dir, "dim_show.csv"), shows,
               ["show_id", "show_name", "show_format"])
    _write_csv(os.path.join(out_dir, "dim_channel.csv"), channels,
               ["channel_id", "channel_name", "channel_type"])
    _write_csv(os.path.join(out_dir, "fact_airtime_segment.csv"), segments,
               ["segment_id", "product_id", "presenter_id", "show_id", "channel_id",
                "start_ts_utc", "end_ts_utc", "segment_duration_minutes"])
    _write_csv(os.path.join(out_dir, "fact_order_line.csv"), order_lines,
               ["order_line_id", "order_id", "product_id", "channel_id", "event_ts_utc",
                "quantity", "unit_price_paid", "gross_amount", "discount_amount", "net_amount"])
    with open(os.path.join(out_dir, "fact_order_line.json"), "w", encoding="utf-8") as jf:
        json.dump(json_order_lines, jf)
    _write_csv(os.path.join(out_dir, "fact_returns.csv"), returns,
               ["return_id", "order_line_id", "returned_qty", "refund_amount",
                "return_ts_utc", "reason_code"])
    _write_csv(os.path.join(out_dir, "fact_traffic.csv"), traffic_rows,
               ["channel_id", "minute_ts_utc", "concurrent_viewers", "web_sessions"])
    # Ground truth for validation only. load_to_snowflake.py never loads _truth_* files.
    truth.sort(key=lambda t: t["order_line_id"])
    _write_csv(os.path.join(out_dir, "_truth_order_attribution.csv"), truth,
               ["order_line_id", "true_segment_id", "order_source"])

    planted = planted_parameters(base_conversion, onair_uplift, halo)
    planted["expected"] = {k: v for k, v in expected.items() if k != "orders_by_channel"}
    total_exp = sum(expected["orders_by_channel"].values())
    planted["expected"]["channel_order_share"] = {
        str(ch): v / total_exp for ch, v in expected["orders_by_channel"].items()} if total_exp else {}

    summary = {
        "window_start_utc": _iso(start),
        "days": days,
        "seed": seed,
        "dim_seed": dim_seed,
        "dim_start_utc": _iso(dim_start or start),
        "dim_days": dim_days or days,
        "counts": {
            "products_versions": len(products),
            "presenters": len(presenters),
            "shows": len(shows),
            "airtime_segments": len(segments),
            "orders": len({o["order_id"] for o in order_lines}),
            "order_lines": len(order_lines),
            "returns": len(returns),
            "traffic_minutes": len(traffic_rows),
        },
        "max_order_id": max((o["order_id"] for o in order_lines), default=order_seq_start),
        "max_order_line_id": max((o["order_line_id"] for o in order_lines), default=order_seq_start * 4),
        "max_segment_id": max((s["segment_id"] for s in segments), default=segment_seq_start),
        "planted": planted,
        "dirty_data": dirt_manifest,
    }
    with open(os.path.join(out_dir, "_batch_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate one batch of synthetic live-commerce data.")
    ap.add_argument("--out", default="generator/output", help="output directory")
    ap.add_argument("--start", default="2026-06-10", help="window start date (UTC, YYYY-MM-DD)")
    ap.add_argument("--days", type=int, default=5, help="number of days to generate")
    ap.add_argument("--seed", type=int, default=42, help="RNG seed")
    ap.add_argument("--products", type=int, default=120, help="number of products")
    ap.add_argument("--presenters", type=int, default=10, help="number of presenters")
    ap.add_argument("--base-conversion", type=float, default=BASE_CONVERSION,
                    help="baseline orders per online session")
    ap.add_argument("--onair-uplift", type=float, default=ONAIR_UPLIFT,
                    help="incremental conversion of exposed sessions, relative to baseline (0 = no effect)")
    ap.add_argument("--halo", type=float, default=HALO,
                    help="online session multiplier while a segment window is open")
    ap.add_argument("--order-seq-start", type=int, default=0,
                    help="order id offset; ids start at this value + 1")
    ap.add_argument("--segment-seq-start", type=int, default=0,
                    help="segment id offset; ids start at this value + 1")
    ap.add_argument("--dirty", action="store_true",
                    help="inject duplicates, nulls, bad types, orphan keys and late timestamps "
                         "into the JSON order-line feed")
    ap.add_argument("--dirty-rate", type=float, default=0.02,
                    help="fraction of rows per corruption type when --dirty is set (default 2%%)")
    ap.add_argument("--quiet", action="store_true", help="don't print the manifest")
    args = ap.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    summary = generate(args.out, start, args.days, args.seed,
                       args.products, args.presenters, args.order_seq_start,
                       dirty=args.dirty, dirty_rate=args.dirty_rate,
                       segment_seq_start=args.segment_seq_start,
                       base_conversion=args.base_conversion,
                       onair_uplift=args.onair_uplift, halo=args.halo)
    if not args.quiet:
        print(json.dumps({k: summary[k] for k in ("window_start_utc", "days", "seed", "counts")}, indent=2))
        print(f"expected measured lift: {summary['planted']['expected']['expected_measured_lift']:+.1%}")


if __name__ == "__main__":
    main()
