"""
Load a generated batch into Snowflake RAW: PUT each file to the internal stage
raw.stg_landing, then COPY INTO the matching RAW table. Credentials come from the
environment or a .env file (see .env.example).

Usage:
    python generator/load_to_snowflake.py --in generator/output
    python generator/load_to_snowflake.py --in generator/output --truncate
"""

from __future__ import annotations

import argparse
import os
import sys

try:
    import snowflake.connector  # type: ignore
except ImportError:
    snowflake = None

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except ImportError:
    pass


# Timestamps are already UTC from the generator, so they land as TIMESTAMP_NTZ.
RAW_TABLES: dict[str, str] = {
    "dim_product": """
        product_id int, product_version int, product_name string, category string,
        brand string, list_price number(12,2), list_price_band string,
        valid_from date, valid_to date, is_current boolean""",
    "dim_presenter": "presenter_id int, presenter_name string, experience_tier string",
    "dim_show": "show_id int, show_name string, show_format string",
    "dim_channel": "channel_id int, channel_name string, channel_type string",
    "fact_airtime_segment": """
        segment_id int, product_id int, presenter_id int, show_id int, channel_id int,
        start_ts_utc timestamp_ntz, end_ts_utc timestamp_ntz, segment_duration_minutes int""",
    "fact_order_line": """
        order_line_id int, order_id int, product_id int, channel_id int,
        event_ts_utc timestamp_ntz, quantity int, unit_price_paid number(12,2),
        gross_amount number(12,2), discount_amount number(12,2), net_amount number(12,2)""",
    "fact_returns": """
        return_id int, order_line_id int, returned_qty int, refund_amount number(12,2),
        return_ts_utc timestamp_ntz, reason_code string""",
    "fact_traffic": """
        channel_id int, minute_ts_utc timestamp_ntz, concurrent_viewers int, web_sessions int""",
}


def _column_names(ddl: str) -> list[str]:
    """Column names from a DDL string, splitting only on top-level commas (number(12,2) has one)."""
    cols, depth, current = [], 0, ""
    for ch in ddl:
        if ch == "(":
            depth += 1
            current += ch
        elif ch == ")":
            depth -= 1
            current += ch
        elif ch == "," and depth == 0:
            cols.append(current)
            current = ""
        else:
            current += ch
    if current.strip():
        cols.append(current)
    return [c.strip().split()[0] for c in cols if c.strip()]


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Missing environment variable {name} (see .env.example)")
    return value


def connect(role: str | None = None, warehouse: str | None = None, schema: str | None = None):
    """Open a connection using password or key-pair auth; arguments override the env defaults."""
    if snowflake is None:
        sys.exit("snowflake-connector-python is not installed. Run: pip install -r requirements.txt")
    params = dict(
        account=_require_env("SNOWFLAKE_ACCOUNT"),
        user=_require_env("SNOWFLAKE_USER"),
        role=role or os.environ.get("SNOWFLAKE_ROLE", "ROLE_LOADER"),
        warehouse=warehouse or os.environ.get("SNOWFLAKE_WAREHOUSE", "WH_LOAD"),
        database=os.environ.get("SNOWFLAKE_DATABASE", "LIVE_COMMERCE"),
        schema=schema or os.environ.get("SNOWFLAKE_SCHEMA", "RAW"),
    )
    key_path = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH")
    if key_path:
        params["private_key_file"] = key_path
        passphrase = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")
        if passphrase:
            params["private_key_file_pwd"] = passphrase
    else:
        params["password"] = _require_env("SNOWFLAKE_PASSWORD")
    return snowflake.connector.connect(**params)


def _rows_loaded(cur) -> int:
    # COPY returns one row per file (file, status, rows_parsed, rows_loaded, ...), or a
    # single status column when every file was already loaded and skipped.
    return sum(r[3] for r in cur.fetchall() if len(r) > 3)


def _put(cur, path: str, stage_dir: str) -> None:
    posix_path = os.path.abspath(path).replace("\\", "/")
    cur.execute(f"put 'file://{posix_path}' {stage_dir}/ overwrite=true auto_compress=true")


def load(in_dir: str, truncate: bool, on_error: str = "abort_statement") -> None:
    conn = connect()
    cur = conn.cursor()
    stage = "@raw.stg_landing"
    try:
        for table, ddl in RAW_TABLES.items():
            csv_path = os.path.join(in_dir, f"{table}.csv")
            if not os.path.exists(csv_path):
                print(f"  skip {table}: no file at {csv_path}")
                continue

            cur.execute(
                f"create table if not exists raw.{table} "
                f"({ddl}, loaded_at_utc timestamp_ntz default sysdate())"
            )
            if truncate:
                cur.execute(f"truncate table raw.{table}")

            _put(cur, csv_path, f"{stage}/{table}")
            cols = _column_names(ddl)
            cur.execute(
                f"copy into raw.{table} ({', '.join(cols)}) "
                f"from {stage}/{table}/ file_format = raw.ff_csv "
                f"pattern = '.*{table}.*\\.csv\\.gz' on_error = '{on_error}'"
            )
            print(f"  loaded raw.{table}: {_rows_loaded(cur)} rows")

        load_variant_order_lines(cur, in_dir, stage, truncate)
        conn.commit()
    finally:
        cur.close()
        conn.close()


def load_variant_order_lines(cur, in_dir: str, stage: str, truncate: bool) -> None:
    """Land the JSON order-line feed as one VARIANT per row; parsing and quarantine happen in dbt."""
    json_path = os.path.join(in_dir, "fact_order_line.json")
    if not os.path.exists(json_path):
        print("  skip order_line_raw (VARIANT): no fact_order_line.json")
        return

    cur.execute(
        "create table if not exists raw.order_line_raw ("
        "  payload variant,"
        "  source_file string,"
        "  loaded_at_utc timestamp_ntz default sysdate()"
        ")"
    )
    if truncate:
        cur.execute("truncate table raw.order_line_raw")

    _put(cur, json_path, f"{stage}/order_line_raw")
    # ff_json uses strip_outer_array, so each array element becomes its own row.
    cur.execute(
        f"copy into raw.order_line_raw (payload, source_file) "
        f"from (select $1, metadata$filename from {stage}/order_line_raw/) "
        f"file_format = raw.ff_json "
        f"pattern = '.*fact_order_line.*\\.json\\.gz' on_error = 'continue'"
    )
    print(f"  loaded raw.order_line_raw (VARIANT): {_rows_loaded(cur)} rows")


def main() -> None:
    ap = argparse.ArgumentParser(description="PUT + COPY INTO generated files to Snowflake RAW.")
    ap.add_argument("--in", dest="in_dir", default="generator/output")
    ap.add_argument("--truncate", action="store_true",
                    help="truncate RAW tables before loading (full reload, not append)")
    ap.add_argument("--on-error", default="abort_statement",
                    choices=["abort_statement", "continue", "skip_file"],
                    help="COPY INTO error handling for the typed CSV loads")
    args = ap.parse_args()
    load(args.in_dir, args.truncate, on_error=args.on_error)


if __name__ == "__main__":
    main()
