"""
Live Commerce — synthetic data generator (one batch).

Generates a self-consistent slice of a live-commerce business with a *real*
airtime -> sales correlation baked in, so the downstream analytics (revenue per
broadcast minute, conversion lift, return rates) produce visible, testable signal.

Why synthetic: no public dataset has a broadcast/airtime dimension. By generating
it ourselves we control the airtime -> sales correlation and can therefore prove the
attribution model works and the statistical lift test detects a real effect.

Outputs (CSV, UTC timestamps throughout) to --out:
    dim_product.csv          one row per product per VERSION (feeds the SCD2 snapshot)
    dim_presenter.csv        one row per presenter
    dim_show.csv             one row per show
    dim_channel.csv          one row per channel (also shipped as a dbt seed)
    fact_airtime_segment.csv one row per broadcast segment
    fact_order_line.csv      one row per product line per order
    fact_returns.csv         one row per returned order line
    fact_traffic.csv         one row per channel per minute

Determinism: a fixed seed makes every run reproducible. The "live" loop varies the
seed and the date window per batch so successive batches add new, non-overlapping rows.

Usage:
    python generator/generate_batch.py --out generator/output
    python generator/generate_batch.py --out generator/output --start 2026-06-10 --days 3 --seed 7
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone

import numpy as np

# ---------------------------------------------------------------------------
# Business configuration — the knobs that define the simulated world.
# ---------------------------------------------------------------------------

CHANNELS = [
    # channel_id, channel_name, channel_type — call-centre + TV + web + app (cross-channel)
    (1, "TV Linear", "broadcast"),
    (2, "Web Shop", "online"),
    (3, "Mobile App", "online"),
    (4, "Call Centre", "phone"),
]

CATEGORIES = [
    # name, base_return_rate (fashion/jewellery are heavy returners)
    ("Fashion", 0.28),
    ("Jewellery", 0.24),
    ("Beauty", 0.10),
    ("Home & Living", 0.08),
    ("Electronics", 0.06),
    ("Wellness", 0.07),
]

PRICE_BANDS = ["budget", "mid", "premium", "luxury"]

SHOWS = [
    # show_name, format
    ("Morning Glow", "magazine"),
    ("Prime Time Live", "flagship"),
    ("Weekend Style Edit", "themed"),
    ("Late Night Deals", "clearance"),
    ("Jewellery Hour", "themed"),
]

EXPERIENCE_TIERS = ["rookie", "established", "star"]

# How strongly airtime lifts conversion, by presenter tier. A "star" presenter
# moving product on prime time is the core live-commerce mechanic. Tuned so the
# on-air vs off-air conversion lift lands in a believable range (tens of %, not
# multiples) — a realistic, defensible signal rather than an implausible spike.
TIER_LIFT_MULTIPLIER = {"rookie": 1.05, "established": 1.25, "star": 1.6}

# Baseline (off-air) conversion rate: orders per session per minute, per channel.
# The lift during airtime is applied on top of this — that gap is what the
# two-proportion z-test (analysis/) is designed to detect.
BASELINE_CONVERSION = {
    "broadcast": 0.0,    # no "sessions" off-air on linear TV; TV orders ride the lift
    "online": 0.012,
    "phone": 0.004,
}

# Minute-level traffic baselines (concurrent viewers / web sessions) by channel type.
BASELINE_TRAFFIC = {
    "broadcast": (4000, 1200),   # (mean concurrent viewers, std)
    "online": (900, 250),
    "phone": (120, 40),
}

# Prime-time daypart (local broadcast convention) lifts traffic AND conversion.
PRIME_TIME_HOURS = set(range(18, 23))  # 18:00–22:59 UTC for this synthetic world
PRIME_TIME_TRAFFIC_MULT = 2.1

LAG_MINUTES = 30  # realistic delay: seen-on-air -> order placed/synced (matches dbt model)


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
# Entity generation
# ---------------------------------------------------------------------------

def _band_for_price(price: float) -> str:
    if price < 30:
        return "budget"
    if price < 100:
        return "mid"
    if price < 300:
        return "premium"
    return "luxury"


# Realistic product names by category — an adjective/material + an item noun, so the
# dashboard reads like a real catalogue (e.g. "Cashmere Wrap Coat") instead of "Product 1".
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
    """A realistic, category-appropriate product name. Falls back gracefully if a
    category has no name pool. The trailing short code keeps names unique."""
    parts = _NAME_PARTS.get(category)
    if not parts:
        return f"Item {pid}"
    adj = parts[0][rng.integers(0, len(parts[0]))]
    noun = parts[1][rng.integers(0, len(parts[1]))]
    return f"{adj} {noun}"


def build_products(rng: np.random.Generator, n_products: int, window_start: datetime) -> list[Product]:
    """Products, some with an SCD2 version change (category reclass or price-band move)
    that lands *before* the analysis window, so the snapshot has real history to track."""
    brands = ["Auriva", "Lumino", "Casa Verde", "NordTech", "Belle Maison", "PureForm"]
    products: list[Product] = []
    for pid in range(1, n_products + 1):
        category = CATEGORIES[rng.integers(0, len(CATEGORIES))][0]
        brand = brands[rng.integers(0, len(brands))]
        # Log-normal price spread. Mean/sigma chosen so all four price bands
        # (budget/mid/premium/luxury) get meaningful coverage across categories —
        # a higher mean pushes a healthy share of products into premium (>=100) and
        # luxury (>=300), so the category x price-band matrix isn't sparse.
        price = float(round(np.exp(rng.normal(4.3, 1.0)), 2))
        price = min(max(price, 7.99), 1999.0)
        band = _band_for_price(price)
        # Realistic name based on the current category; stable across this product's versions.
        name = _product_name(rng, category, pid)

        # ~25% of products underwent a change before the window: a reclassification
        # or a price-band move. That earlier version is the SCD2 "expired" row.
        changed = rng.random() < 0.25
        if changed:
            old_valid_from = (window_start - timedelta(days=120)).date().isoformat()
            change_date = (window_start - timedelta(days=30)).date().isoformat()
            # The old version: a different category or a lower price band.
            if rng.random() < 0.5:
                old_category = CATEGORIES[rng.integers(0, len(CATEGORIES))][0]
                old_price = price
            else:
                old_category = category
                old_price = float(round(price * rng.uniform(0.55, 0.8), 2))
            products.append(Product(
                product_id=pid, product_version=1, product_name=name,
                category=old_category, brand=brand, list_price=old_price,
                list_price_band=_band_for_price(old_price),
                valid_from=old_valid_from, valid_to=change_date, is_current=False,
            ))
            products.append(Product(
                product_id=pid, product_version=2, product_name=name,
                category=category, brand=brand, list_price=price, list_price_band=band,
                valid_from=change_date, valid_to="9999-12-31", is_current=True,
            ))
        else:
            products.append(Product(
                product_id=pid, product_version=1, product_name=name,
                category=category, brand=brand, list_price=price, list_price_band=band,
                valid_from=(window_start - timedelta(days=120)).date().isoformat(),
                valid_to="9999-12-31", is_current=True,
            ))
    return products


def build_presenters(rng: np.random.Generator, n: int) -> list[dict]:
    first = ["Anja", "Markus", "Lena", "Tobias", "Sofia", "Jonas", "Clara", "Felix",
             "Nina", "David", "Mara", "Paul"]
    presenters = []
    for i in range(1, n + 1):
        tier = EXPERIENCE_TIERS[min(int(rng.integers(0, 3) + rng.integers(0, 2)) // 1, 2)]
        # weight toward 'established'
        tier = rng.choice(EXPERIENCE_TIERS, p=[0.3, 0.5, 0.2])
        presenters.append({
            "presenter_id": i,
            "presenter_name": f"{first[(i - 1) % len(first)]} P{i}",
            "experience_tier": str(tier),
        })
    return presenters


def build_shows() -> list[dict]:
    return [{"show_id": i + 1, "show_name": n, "show_format": f}
            for i, (n, f) in enumerate(SHOWS)]


# ---------------------------------------------------------------------------
# Airtime schedule — the broadcast plan that everything else correlates to.
# ---------------------------------------------------------------------------

def build_airtime_segments(
    rng: np.random.Generator,
    products: list[Product],
    presenters: list[dict],
    shows: list[dict],
    window_start: datetime,
    days: int,
) -> list[dict]:
    """A broadcast schedule: each day has shows; each show airs a sequence of
    products in back-to-back segments. A segment = one product featured in one
    continuous on-air window. Always aired on the broadcast (TV) channel."""
    current_products = [p for p in products if p.is_current]
    segments: list[dict] = []
    seg_id = 0
    tv_channel_id = 1

    for d in range(days):
        day = window_start + timedelta(days=d)
        # 3–5 shows per day, spread across the broadcast day, biased to prime time.
        n_shows = int(rng.integers(3, 6))
        show_start_hours = sorted(rng.choice(
            list(range(8, 23)), size=n_shows, replace=False,
            p=_hour_weights(),
        ))
        for sh in show_start_hours:
            show = shows[rng.integers(0, len(shows))]
            presenter = presenters[rng.integers(0, len(presenters))]
            cursor = day.replace(hour=int(sh), minute=0, second=0, microsecond=0)
            # A show runs 60–120 min, filled with 5–14 min product segments.
            show_minutes = int(rng.integers(60, 121))
            elapsed = 0
            while elapsed < show_minutes - 5:
                dur = int(rng.integers(5, 15))
                if elapsed + dur > show_minutes:
                    dur = show_minutes - elapsed
                product = current_products[rng.integers(0, len(current_products))]
                start = cursor + timedelta(minutes=elapsed)
                end = start + timedelta(minutes=dur)
                seg_id += 1
                segments.append({
                    "segment_id": seg_id,
                    "product_id": product.product_id,
                    "presenter_id": presenter["presenter_id"],
                    "show_id": show["show_id"],
                    "channel_id": tv_channel_id,
                    "start_ts_utc": _iso(start),
                    "end_ts_utc": _iso(end),
                    "segment_duration_minutes": dur,
                })
                elapsed += dur
    return segments


def _hour_weights() -> np.ndarray:
    hours = list(range(8, 23))
    w = np.array([1.0 if h not in PRIME_TIME_HOURS else 2.5 for h in hours])
    return w / w.sum()


# ---------------------------------------------------------------------------
# Traffic — the conversion denominator. Rises during airtime (and prime time),
# so the lift the orders show up against is a real rate change, not just volume.
# ---------------------------------------------------------------------------

def build_traffic(
    rng: np.random.Generator,
    segments: list[dict],
    window_start: datetime,
    days: int,
) -> list[dict]:
    """One row per channel per minute. Online/app/phone traffic gets an additive
    bump during any active TV segment (cross-channel halo of going on air)."""
    # Pre-index minutes that are "on air" so we can apply the halo.
    on_air_minutes: set[int] = set()
    for s in segments:
        st = _parse(s["start_ts_utc"])
        en = _parse(s["end_ts_utc"]) + timedelta(minutes=LAG_MINUTES)
        m = st
        while m < en:
            on_air_minutes.add(int(m.timestamp()) // 60)
            m += timedelta(minutes=1)

    rows: list[dict] = []
    total_minutes = days * 24 * 60
    for ch_id, ch_name, ch_type in CHANNELS:
        base_v, base_std = BASELINE_TRAFFIC[ch_type]
        for i in range(total_minutes):
            minute = window_start + timedelta(minutes=i)
            prime = minute.hour in PRIME_TIME_HOURS
            mult = PRIME_TIME_TRAFFIC_MULT if prime else 1.0
            halo = 1.0
            if ch_type == "online" and (int(minute.timestamp()) // 60) in on_air_minutes:
                halo = 1.8  # cross-channel halo: TV airing drives web/app traffic
            mean = base_v * mult * halo
            viewers = max(0, int(rng.normal(mean, base_std)))
            # web_sessions: for online channels viewers ~= sessions; broadcast has
            # "viewers" but no web sessions; phone has call volume as sessions.
            if ch_type == "online":
                sessions = viewers
            elif ch_type == "phone":
                sessions = max(0, int(viewers))
            else:  # broadcast: concurrent viewers, sessions n/a (0)
                sessions = 0
            rows.append({
                "channel_id": ch_id,
                "minute_ts_utc": _iso(minute),
                "concurrent_viewers": viewers if ch_type == "broadcast" else 0,
                "web_sessions": sessions,
            })
    return rows


# ---------------------------------------------------------------------------
# Orders — the core correlation. Most orders happen inside a product's airtime
# window (+lag), on channels other than TV, scaled by presenter tier and traffic.
# A baseline trickle of organic orders happens off-air -> attributed to segment -1.
# ---------------------------------------------------------------------------

def build_orders_and_returns(
    rng: np.random.Generator,
    products: list[Product],
    presenters: list[dict],
    segments: list[dict],
    traffic_index: dict[tuple[int, int], int],
    window_start: datetime,
    days: int,
    order_seq_start: int,
) -> tuple[list[dict], list[dict]]:
    """Returns (order_lines, returns)."""
    presenter_tier = {p["presenter_id"]: p["experience_tier"] for p in presenters}
    cat_return_rate = dict(CATEGORIES)
    current_by_id = {p.product_id: p for p in products if p.is_current}
    online_channels = [c[0] for c in CHANNELS if c[2] in ("online", "phone")]
    tv_channel_id = 1

    order_lines: list[dict] = []
    returns: list[dict] = []
    order_id = order_seq_start
    line_id = order_seq_start * 4
    return_id = order_seq_start * 2

    # --- Airtime-driven orders -------------------------------------------------
    for seg in segments:
        product = current_by_id.get(seg["product_id"])
        if product is None:
            continue
        tier = presenter_tier[seg["presenter_id"]]
        lift = TIER_LIFT_MULTIPLIER[tier]
        st = _parse(seg["start_ts_utc"])
        en = _parse(seg["end_ts_utc"])
        prime = st.hour in PRIME_TIME_HOURS
        # Expected orders this segment: duration * lift * prime bump * price-attractiveness.
        price_factor = np.clip(2.0 - (product.list_price / 400.0), 0.4, 1.8)
        expected = (seg["segment_duration_minutes"] * lift
                    * (1.6 if prime else 1.0) * price_factor * rng.uniform(0.8, 1.2))
        n_orders = int(rng.poisson(max(expected, 0.1)))

        for _ in range(n_orders):
            # Order time: inside [start, end+lag], front-loaded (most buy while it airs).
            offset = min(
                rng.exponential(seg["segment_duration_minutes"] * 0.6),
                seg["segment_duration_minutes"] + LAG_MINUTES,
            )
            ts = st + timedelta(minutes=float(offset))
            # Channel: airtime drives phone + online; TV itself takes call-centre orders.
            channel_id = int(rng.choice(online_channels + [tv_channel_id],
                                        p=_order_channel_probs(len(online_channels))))
            order_id += 1
            line_id += 1
            ol = _make_order_line(rng, line_id, order_id, product, channel_id, ts)
            order_lines.append(ol)
            _maybe_return(rng, ol, product, cat_return_rate, returns, return_id_ref := [return_id])
            return_id = return_id_ref[0]

    # --- Organic / baseline orders (off-air) -> will attribute to segment -1 ----
    # A steady baseline of organic demand across online channels, independent of
    # airtime. Sized so off-air conversion is a realistic baseline and the on-air
    # lift over it lands in a believable tens-of-percent range (not a 20x spike).
    n_organic = int(days * 24 * 60 * 0.13 * len(online_channels))
    current_products = list(current_by_id.values())
    for _ in range(n_organic):
        minute_offset = int(rng.integers(0, days * 24 * 60))
        ts = window_start + timedelta(minutes=minute_offset)
        product = current_products[rng.integers(0, len(current_products))]
        channel_id = int(rng.choice(online_channels))
        order_id += 1
        line_id += 1
        ol = _make_order_line(rng, line_id, order_id, product, channel_id, ts)
        order_lines.append(ol)
        _maybe_return(rng, ol, product, cat_return_rate, returns, ref := [return_id])
        return_id = ref[0]

    return order_lines, returns


def _order_channel_probs(n_online: int) -> list[float]:
    # online/phone channels share most weight; TV (call-centre proxy) gets the rest.
    base = [0.55, 0.30][:n_online]
    if len(base) < n_online:
        base += [0.10] * (n_online - len(base))
    s = sum(base)
    base = [b / s * 0.85 for b in base]
    return base + [0.15]


def _make_order_line(rng, line_id, order_id, product: Product, channel_id, ts) -> dict:
    qty = int(rng.choice([1, 1, 1, 2, 2, 3], p=[0.45, 0.2, 0.1, 0.13, 0.07, 0.05]))
    # Paid price varies around list price (promotions) — this lives in the FACT.
    unit_price_paid = round(product.list_price * rng.uniform(0.7, 1.0), 2)
    gross = round(unit_price_paid * qty, 2)
    discount = round(gross * rng.uniform(0.0, 0.15), 2)
    net = round(gross - discount, 2)
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
        "net_amount": net,
    }


def _maybe_return(rng, ol: dict, product: Product, cat_return_rate, returns: list, return_id_ref: list):
    rate = cat_return_rate.get(product.category, 0.1)
    # luxury/premium return a bit more (fit/expectation gap).
    if product.list_price_band in ("premium", "luxury"):
        rate *= 1.2
    if rng.random() < rate:
        returned_qty = int(rng.integers(1, ol["quantity"] + 1))
        refund = round(ol["net_amount"] * (returned_qty / ol["quantity"]), 2)
        return_id_ref[0] += 1
        # Return happens 1–21 days after the order.
        ret_ts = _parse(ol["event_ts_utc"]) + timedelta(days=float(rng.uniform(1, 21)))
        reason = str(rng.choice(
            ["size_fit", "not_as_described", "changed_mind", "damaged", "late_delivery"],
            p=[0.4, 0.2, 0.25, 0.1, 0.05],
        ))
        returns.append({
            "return_id": return_id_ref[0],
            "order_line_id": ol["order_line_id"],
            "returned_qty": returned_qty,
            "refund_amount": refund,
            "return_ts_utc": _iso(ret_ts),
            "reason_code": reason,
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


def _write_csv(path: str, rows: list[dict], fieldnames: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------------------
# Dirty-data injection (production-realism mode)
# ---------------------------------------------------------------------------

def inject_dirt(rng: np.random.Generator, order_lines: list[dict], traffic: list[dict],
                n_products: int, dirty_rate: float) -> dict:
    """Corrupt a fraction of rows the way real multi-source feeds do, so the pipeline can
    PROVE it survives the worst case rather than assuming clean input. Returns a manifest
    of exactly what was injected, so tests and docs can assert the pipeline handled it.

    Each dirt type maps to a downstream defence:
      duplicate row      -> staging dedup (row_number) + uniqueness test
      null required key  -> not_null test fires / staging filters
      bad numeric type   -> VARIANT schema-on-read + try_cast in staging
      orphan FK          -> unknown member (-1) + relationships test
      out-of-order/late  -> incremental lookback re-attribution
      whitespace/case    -> trim()/lower() in staging
    """
    if not order_lines:
        return {"injected": False}

    manifest: dict = {"injected": True, "dirty_rate": dirty_rate, "types": {}}
    n = len(order_lines)
    k = max(1, int(n * dirty_rate))

    def sample_indices(count: int) -> list[int]:
        return list(rng.choice(n, size=min(count, n), replace=False))

    # 1. Duplicate rows (exact dupes appended) — same order_line_id reused.
    dup_src = sample_indices(k)
    dupes = [dict(order_lines[i]) for i in dup_src]
    order_lines.extend(dupes)
    manifest["types"]["duplicate_order_lines"] = len(dupes)

    # 2. Null in a required field (product_id) on some rows.
    null_idx = sample_indices(k)
    for i in null_idx:
        order_lines[i]["product_id"] = ""
    manifest["types"]["null_product_id"] = len(null_idx)

    # 3. Bad numeric type — quantity as a junk string (un-parseable).
    bad_idx = sample_indices(k)
    for i in bad_idx:
        order_lines[i]["quantity"] = rng.choice(["N/A", "two", "-", "??"])
    manifest["types"]["bad_quantity_type"] = len(bad_idx)

    # 4. Orphan FK — product_id that does not exist in dim_product.
    orphan_idx = sample_indices(k)
    for i in orphan_idx:
        order_lines[i]["product_id"] = n_products + 9999
    manifest["types"]["orphan_product_id"] = len(orphan_idx)

    # 5. Out-of-order / late timestamps — shift some events far back/forward.
    late_idx = sample_indices(k)
    for i in late_idx:
        ts = _parse(order_lines[i]["event_ts_utc"])
        order_lines[i]["event_ts_utc"] = _iso(ts - timedelta(days=float(rng.uniform(2, 10))))
    manifest["types"]["out_of_order_timestamps"] = len(late_idx)

    # 6. Whitespace / case noise on a categorical-ish field (channel left as id, so use
    #    reason-style noise on net_amount formatting is risky; instead pad order_id text).
    #    Apply whitespace to the traffic web_sessions occasionally as stringy noise.
    if traffic:
        tn = len(traffic)
        tk = max(1, int(tn * dirty_rate * 0.5))
        for i in rng.choice(tn, size=min(tk, tn), replace=False):
            traffic[i]["web_sessions"] = f"  {traffic[i]['web_sessions']} "
        manifest["types"]["whitespace_web_sessions"] = int(min(tk, tn))

    return manifest


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def generate(out_dir: str, start: datetime, days: int, seed: int,
             n_products: int, n_presenters: int, order_seq_start: int,
             dirty: bool = False, dirty_rate: float = 0.02) -> dict:
    rng = np.random.default_rng(seed)
    os.makedirs(out_dir, exist_ok=True)

    products = build_products(rng, n_products, start)
    presenters = build_presenters(rng, n_presenters)
    shows = build_shows()
    segments = build_airtime_segments(rng, products, presenters, shows, start, days)
    traffic = build_traffic(rng, segments, start, days)
    # index not strictly needed downstream but kept for future conversion shaping
    order_lines, returns = build_orders_and_returns(
        rng, products, presenters, segments, {}, start, days, order_seq_start,
    )

    # Production-realism: optionally corrupt a fraction of rows so the pipeline must
    # prove it handles dirty multi-source data, not just the happy path.
    dirt_manifest = {"injected": False}
    if dirty:
        dirt_manifest = inject_dirt(rng, order_lines, traffic, n_products, dirty_rate)

    # dim_channel ships as a dbt seed too, but write here for completeness.
    channels = [{"channel_id": c, "channel_name": n, "channel_type": t} for c, n, t in CHANNELS]

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
    # Also emit order lines as semi-structured JSON — simulating an event/app feed that
    # arrives without a schema guarantee. The loader lands this as VARIANT (schema-on-read)
    # so a source field change never breaks the load; staging parses + quarantines.
    with open(os.path.join(out_dir, "fact_order_line.json"), "w", encoding="utf-8") as jf:
        json.dump(order_lines, jf)
    _write_csv(os.path.join(out_dir, "fact_returns.csv"), returns,
               ["return_id", "order_line_id", "returned_qty", "refund_amount",
                "return_ts_utc", "reason_code"])
    _write_csv(os.path.join(out_dir, "fact_traffic.csv"), traffic,
               ["channel_id", "minute_ts_utc", "concurrent_viewers", "web_sessions"])

    summary = {
        "window_start_utc": _iso(start),
        "days": days,
        "seed": seed,
        "counts": {
            "products_versions": len(products),
            "presenters": len(presenters),
            "shows": len(shows),
            "airtime_segments": len(segments),
            "order_lines": len(order_lines),
            "returns": len(returns),
            "traffic_minutes": len(traffic),
        },
        "max_order_id": max((o["order_id"] for o in order_lines), default=order_seq_start),
        "max_order_line_id": max((o["order_line_id"] for o in order_lines), default=order_seq_start * 4),
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
    ap.add_argument("--seed", type=int, default=42, help="RNG seed (reproducibility)")
    ap.add_argument("--products", type=int, default=120, help="number of products")
    ap.add_argument("--presenters", type=int, default=10, help="number of presenters")
    ap.add_argument("--order-seq-start", type=int, default=0,
                    help="starting id offset (the live loop bumps this so batches don't collide)")
    ap.add_argument("--dirty", action="store_true",
                    help="inject realistic dirty data (dupes, nulls, bad types, orphans, "
                         "out-of-order timestamps) so the pipeline must prove it handles the worst case")
    ap.add_argument("--dirty-rate", type=float, default=0.02,
                    help="fraction of rows to corrupt when --dirty is set (default 2%%)")
    args = ap.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    summary = generate(args.out, start, args.days, args.seed,
                       args.products, args.presenters, args.order_seq_start,
                       dirty=args.dirty, dirty_rate=args.dirty_rate)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
