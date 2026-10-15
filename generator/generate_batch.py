"""
Generate one batch of synthetic live-commerce data with an airtime -> sales effect built in.

Writes dimension and fact CSVs (UTC timestamps), a JSON copy of the order lines for the
VARIANT feed, and _batch_manifest.json to --out. A fixed seed makes runs reproducible.

Usage:
    python generator/generate_batch.py --out generator/output
    python generator/generate_batch.py --out generator/output --start 2026-06-10 --days 3 --seed 7
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
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

CATEGORIES = [
    # name, base_return_rate
    ("Fashion", 0.28),
    ("Jewellery", 0.24),
    ("Beauty", 0.10),
    ("Home & Living", 0.08),
    ("Electronics", 0.06),
    ("Wellness", 0.07),
]

SHOWS = [
    # show_name, format
    ("Morning Glow", "magazine"),
    ("Prime Time Live", "flagship"),
    ("Weekend Style Edit", "themed"),
    ("Late Night Deals", "clearance"),
    ("Jewellery Hour", "themed"),
]

EXPERIENCE_TIERS = ["rookie", "established", "star"]

# Expected airtime orders per segment minute, by presenter tier.
TIER_LIFT_MULTIPLIER = {"rookie": 1.05, "established": 1.25, "star": 1.6}

# (mean, std) of concurrent viewers / sessions per minute, by channel type.
BASELINE_TRAFFIC = {
    "broadcast": (4000, 1200),
    "online": (900, 250),
    "phone": (120, 40),
}

PRIME_TIME_HOURS = set(range(18, 23))  # 18:00-22:59 UTC
PRIME_TIME_TRAFFIC_MULT = 2.1

LAG_MINUTES = 30  # must match dbt var attribution_lag_minutes


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


def build_products(rng: np.random.Generator, n_products: int, window_start: datetime) -> list[Product]:
    """Products; ~25% get an earlier SCD2 version that expired before the window."""
    brands = ["Auriva", "Lumino", "Casa Verde", "NordTech", "Belle Maison", "PureForm"]
    products: list[Product] = []
    for pid in range(1, n_products + 1):
        category = CATEGORIES[rng.integers(0, len(CATEGORIES))][0]
        brand = brands[rng.integers(0, len(brands))]
        # Log-normal prices; parameters chosen so every price band is well populated.
        price = float(round(np.exp(rng.normal(4.3, 1.0)), 2))
        price = min(max(price, 7.99), 1999.0)
        band = _band_for_price(price)
        name = _product_name(rng, category, pid)

        changed = rng.random() < 0.25
        if changed:
            old_valid_from = (window_start - timedelta(days=120)).date().isoformat()
            change_date = (window_start - timedelta(days=30)).date().isoformat()
            # Old version differs by category or by a lower price.
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
# Facts
# ---------------------------------------------------------------------------

def build_airtime_segments(
    rng: np.random.Generator,
    products: list[Product],
    presenters: list[dict],
    shows: list[dict],
    window_start: datetime,
    days: int,
    segment_seq_start: int = 0,
) -> list[dict]:
    """Broadcast schedule: 3-5 shows a day, each a run of back-to-back product segments on TV."""
    current_products = [p for p in products if p.is_current]
    segments: list[dict] = []
    seg_id = segment_seq_start
    tv_channel_id = 1

    for d in range(days):
        day = window_start + timedelta(days=d)
        n_shows = int(rng.integers(3, 6))
        show_start_hours = sorted(rng.choice(
            list(range(8, 23)), size=n_shows, replace=False,
            p=_hour_weights(),
        ))
        for sh in show_start_hours:
            show = shows[rng.integers(0, len(shows))]
            presenter = presenters[rng.integers(0, len(presenters))]
            cursor = day.replace(hour=int(sh), minute=0, second=0, microsecond=0)
            # 60-120 minute show made of 5-14 minute segments.
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


def build_traffic(
    rng: np.random.Generator,
    segments: list[dict],
    window_start: datetime,
    days: int,
) -> list[dict]:
    """One row per channel per minute; online traffic gets a halo while any segment is on air."""
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
                halo = 2.9
            mean = base_v * mult * halo
            viewers = max(0, int(rng.normal(mean, base_std)))
            # Online: sessions == visitors. Phone: call volume. Broadcast: no sessions.
            if ch_type == "online":
                sessions = viewers
            elif ch_type == "phone":
                sessions = max(0, int(viewers))
            else:
                sessions = 0
            rows.append({
                "channel_id": ch_id,
                "minute_ts_utc": _iso(minute),
                "concurrent_viewers": viewers if ch_type == "broadcast" else 0,
                "web_sessions": sessions,
            })
    return rows


def build_orders_and_returns(
    rng: np.random.Generator,
    products: list[Product],
    presenters: list[dict],
    segments: list[dict],
    window_start: datetime,
    days: int,
    order_seq_start: int,
) -> tuple[list[dict], list[dict]]:
    """Airtime-driven orders per segment plus a uniform off-air baseline. Returns (order_lines, returns)."""
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

    for seg in segments:
        product = current_by_id.get(seg["product_id"])
        if product is None:
            continue
        tier = presenter_tier[seg["presenter_id"]]
        lift = TIER_LIFT_MULTIPLIER[tier]
        st = _parse(seg["start_ts_utc"])
        prime = st.hour in PRIME_TIME_HOURS
        price_factor = np.clip(2.0 - (product.list_price / 400.0), 0.4, 1.8)
        expected = (seg["segment_duration_minutes"] * lift
                    * (1.6 if prime else 1.0) * price_factor * rng.uniform(0.8, 1.2))
        n_orders = int(rng.poisson(max(expected, 0.1)))

        for _ in range(n_orders):
            # Front-loaded order time, capped one minute inside the half-open
            # [start, end + lag) window so every airtime order attributes.
            offset = min(
                rng.exponential(seg["segment_duration_minutes"] * 0.6),
                seg["segment_duration_minutes"] + LAG_MINUTES - 1,
            )
            ts = st + timedelta(minutes=float(offset))
            channel_id = int(rng.choice(online_channels + [tv_channel_id],
                                        p=_order_channel_probs(len(online_channels))))
            order_id += 1
            line_id += 1
            ol = _make_order_line(rng, line_id, order_id, product, channel_id, ts)
            order_lines.append(ol)
            _maybe_return(rng, ol, product, cat_return_rate, returns, return_id_ref := [return_id])
            return_id = return_id_ref[0]

    # Organic orders spread uniformly over the window, independent of airtime.
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
    # Non-TV channels share 85%; TV (call-in orders) takes the remaining 15%.
    base = [0.55, 0.30][:n_online]
    if len(base) < n_online:
        base += [0.10] * (n_online - len(base))
    s = sum(base)
    base = [b / s * 0.85 for b in base]
    return base + [0.15]


def _make_order_line(rng, line_id, order_id, product: Product, channel_id, ts) -> dict:
    qty = int(rng.choice([1, 1, 1, 2, 2, 3], p=[0.45, 0.2, 0.1, 0.13, 0.07, 0.05]))
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
    if product.list_price_band in ("premium", "luxury"):
        rate *= 1.2
    if rng.random() < rate:
        returned_qty = int(rng.integers(1, ol["quantity"] + 1))
        refund = round(ol["net_amount"] * (returned_qty / ol["quantity"]), 2)
        return_id_ref[0] += 1
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

def generate(out_dir: str, start: datetime, days: int, seed: int,
             n_products: int, n_presenters: int, order_seq_start: int,
             dirty: bool = False, dirty_rate: float = 0.02,
             segment_seq_start: int = 0, dim_seed: int | None = None,
             dim_start: datetime | None = None) -> dict:
    """Generate one batch into out_dir and return its manifest.

    dim_seed / dim_start fix the dimension tables independently of the fact seed and
    window, so successive live batches describe the same products and presenters.
    """
    os.makedirs(out_dir, exist_ok=True)
    if dim_seed is None:
        dim_seed = seed
    dim_rng = np.random.default_rng(dim_seed)
    products = build_products(dim_rng, n_products, dim_start or start)
    presenters = build_presenters(dim_rng, n_presenters)
    shows = build_shows()
    # With a single seed, keep one RNG stream so standalone output stays reproducible.
    rng = dim_rng if dim_seed == seed else np.random.default_rng(seed)

    segments = build_airtime_segments(rng, products, presenters, shows, start, days,
                                      segment_seq_start)
    traffic = build_traffic(rng, segments, start, days)
    order_lines, returns = build_orders_and_returns(
        rng, products, presenters, segments, start, days, order_seq_start,
    )

    # Dirt goes only into the JSON feed (landed as VARIANT); the typed CSVs stay clean
    # so a strict COPY INTO never aborts on them.
    json_order_lines = order_lines
    dirt_manifest = {"injected": False}
    if dirty:
        json_order_lines = copy.deepcopy(order_lines)
        dirt_manifest = inject_dirt(rng, json_order_lines, n_products, dirty_rate)

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
    with open(os.path.join(out_dir, "fact_order_line.json"), "w", encoding="utf-8") as jf:
        json.dump(json_order_lines, jf)
    _write_csv(os.path.join(out_dir, "fact_returns.csv"), returns,
               ["return_id", "order_line_id", "returned_qty", "refund_amount",
                "return_ts_utc", "reason_code"])
    _write_csv(os.path.join(out_dir, "fact_traffic.csv"), traffic,
               ["channel_id", "minute_ts_utc", "concurrent_viewers", "web_sessions"])

    summary = {
        "window_start_utc": _iso(start),
        "days": days,
        "seed": seed,
        "dim_seed": dim_seed,
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
        "max_segment_id": max((s["segment_id"] for s in segments), default=segment_seq_start),
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
    ap.add_argument("--order-seq-start", type=int, default=0,
                    help="order id offset; ids start at this value + 1")
    ap.add_argument("--segment-seq-start", type=int, default=0,
                    help="segment id offset; ids start at this value + 1")
    ap.add_argument("--dirty", action="store_true",
                    help="inject duplicates, nulls, bad types, orphan keys and late timestamps "
                         "into the JSON order-line feed")
    ap.add_argument("--dirty-rate", type=float, default=0.02,
                    help="fraction of rows per corruption type when --dirty is set (default 2%%)")
    args = ap.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    summary = generate(args.out, start, args.days, args.seed,
                       args.products, args.presenters, args.order_seq_start,
                       dirty=args.dirty, dirty_rate=args.dirty_rate,
                       segment_seq_start=args.segment_seq_start)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
