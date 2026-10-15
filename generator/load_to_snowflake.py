"""
Load generated CSVs into Snowflake RAW: PUT to internal stage, then COPY INTO.

Implements the ingestion design from :
    generator -> files -> PUT (internal stage stg_landing) -> COPY INTO RAW.<table>

RAW tables are created here (typed, with a loaded_at_utc audit column). The
generator output is the contract; dbt staging cleans/dedupes from RAW onward.

Credentials come from environment variables (never committed). Copy .env.example
to .env and fill it in, or export them in your shell:

    SNOWFLAKE_ACCOUNT   e.g. ab12345.eu-central-1
    SNOWFLAKE_USER
    SNOWFLAKE_PASSWORD  (or set SNOWFLAKE_PRIVATE_KEY_PATH for key-pair auth)
    SNOWFLAKE_ROLE       default ROLE_LOADER
    SNOWFLAKE_WAREHOUSE  default WH_LOAD
    SNOWFLAKE_DATABASE   default LIVE_COMMERCE
    SNOWFLAKE_SCHEMA     default RAW

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
    snowflake = None  # tolerated until the dependency is installed

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except ImportError:
    pass


# RAW table DDL. Timestamps land as TIMESTAMP_NTZ (already UTC from the generator);
# the generator's contract guarantees the values are UTC, so NTZ keeps it explicit
# that no zone conversion happens before the BI layer (UTC everywhere).
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
    """Extract column names from a DDL string, splitting only on TOP-LEVEL commas so
    that types like number(12,2) (which contain a comma) are not split mid-type."""
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
    # First whitespace-delimited token of each definition is the column name.
    return [c.strip().split()[0] for c in cols if c.strip()]


def connect():
    if snowflake is None:
        sys.exit("snowflake-connector-python is not installed. Run: pip install -r requirements.txt")
    params = dict(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        role=os.environ.get("SNOWFLAKE_ROLE", "ROLE_LOADER"),
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "WH_LOAD"),
        database=os.environ.get("SNOWFLAKE_DATABASE", "LIVE_COMMERCE"),
        schema=os.environ.get("SNOWFLAKE_SCHEMA", "RAW"),
    )
    key_path = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH")
    if key_path:
        params["private_key_file"] = key_path
    else:
        params["password"] = os.environ["SNOWFLAKE_PASSWORD"]
    return snowflake.connector.connect(**params)


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

            # PUT overwrites the staged file; auto_compress gzips it on upload.
            posix_path = csv_path.replace("\\", "/")
            cur.execute(
                f"put 'file://{posix_path}' {stage}/{table}/ overwrite=true auto_compress=true"
            )
            # COPY INTO with the named CSV format; explicit column list keeps it
            # resilient to the audit column ordering. on_error is configurable:
            #   abort_statement  -> fail fast (strict typed feeds)
            #   continue         -> load good rows, skip bad (resilient feeds)
            cols = _column_names(ddl)
            cur.execute(
                f"copy into raw.{table} ({', '.join(cols)}) "
                f"from {stage}/{table}/ file_format = raw.ff_csv "
                f"pattern = '.*{table}.*\\.csv\\.gz' on_error = '{on_error}'"
            )
            rows = cur.fetchall()
            loaded = sum(r[2] for r in rows) if rows else 0
            print(f"  loaded raw.{table}: {loaded} rows")

        load_variant_order_lines(cur, in_dir, stage, truncate)
        conn.commit()
    finally:
        cur.close()
        conn.close()


def load_variant_order_lines(cur, in_dir: str, stage: str, truncate: bool) -> None:
    """Land the JSON order-line feed as VARIANT (schema-on-read).

    Why: a typed CSV table breaks the load the moment a source adds/removes/renames a
    field or sends a bad type. Landing the raw payload as a single VARIANT column means
    the load NEVER breaks on a schema change — we parse (and validate/quarantine) in
    staging instead. This is the resilient-ingestion pattern for semi-structured feeds.
    """
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

    posix_path = json_path.replace("\\", "/")
    cur.execute(
        f"put 'file://{posix_path}' {stage}/order_line_raw/ overwrite=true auto_compress=true"
    )
    # strip_outer_array (ff_json) turns the JSON array into one VARIANT row per element.
    cur.execute(
        f"copy into raw.order_line_raw (payload, source_file) "
        f"from (select $1, metadata$filename from {stage}/order_line_raw/) "
        f"file_format = raw.ff_json "
        f"pattern = '.*fact_order_line.*\\.json\\.gz' on_error = 'continue'"
    )
    rows = cur.fetchall()
    loaded = sum(r[2] for r in rows) if rows else 0
    print(f"  loaded raw.order_line_raw (VARIANT): {loaded} rows")


def main() -> None:
    ap = argparse.ArgumentParser(description="PUT + COPY INTO generated CSVs to Snowflake RAW.")
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
