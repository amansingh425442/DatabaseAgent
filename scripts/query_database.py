"""Run a human-written query against local Olist data; never an agent tool."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from dotenv import dotenv_values
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from configure_reader import local_url, READER_ROLE, ROOT


MAX_ROWS = 500
TIMEOUT_MS = 10_000


def row_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("limit must be an integer") from None
    if not 1 <= limit <= MAX_ROWS:
        raise argparse.ArgumentTypeError(f"limit must be between 1 and {MAX_ROWS}")
    return limit


def query_rows(engine, statement: str, limit: int = MAX_ROWS) -> tuple[pd.DataFrame, bool]:
    if not statement.strip():
        raise ValueError("The SQL query is empty.")
    if not 1 <= limit <= MAX_ROWS:
        raise ValueError(f"limit must be between 1 and {MAX_ROWS}.")
    with engine.connect() as conn, conn.begin():
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        conn.execute(text("SELECT set_config('statement_timeout', :timeout, true)"),
                     {"timeout": str(TIMEOUT_MS)})
        # A PostgreSQL server-side cursor fetches only the bounded result and
        # accepts one row-producing query, avoiding client-side full downloads.
        with conn.execution_options(stream_results=True, no_parameters=True).exec_driver_sql(statement) as result:
            if not result.returns_rows:
                raise ValueError("Submit one SELECT or row-producing WITH query.")
            columns = list(result.keys())
            rows = result.fetchmany(limit + 1)
            truncated = len(rows) > limit
            frame = pd.DataFrame.from_records([tuple(row) for row in rows[:limit]], columns=columns)
    return frame, truncated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--sql", help="one human-written SELECT or WITH query")
    source.add_argument("--file", type=Path, help="UTF-8 file containing one query")
    parser.add_argument("--limit", type=row_limit, default=MAX_ROWS,
                        help=f"maximum rows to display (1-{MAX_ROWS}; default {MAX_ROWS})")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    engine = None
    try:
        values = dotenv_values(args.env_file, interpolate=False)
        reader = local_url(values.get("READER_DATABASE_URL"), "READER_DATABASE_URL")
        if reader.username != READER_ROLE:
            raise ValueError("READER_DATABASE_URL must use the dedicated olist_reader role.")
        statement = args.sql if args.sql is not None else args.file.read_text(encoding="utf-8-sig")
        engine = create_engine(reader, pool_pre_ping=True, connect_args={"connect_timeout": 30})
        frame, truncated = query_rows(engine, statement, args.limit)
        print(frame.to_string(index=False, na_rep="NULL"))
        print(f"\n{len(frame)} row(s) displayed.")
        if truncated:
            print(f"TRUNCATED: more than {args.limit} rows matched. "
                  "Add filters or aggregate in SQL to inspect the complete result.")
    except (ValueError, OSError) as exc:
        print(f"Query failed: {exc}", file=sys.stderr)
        return 1
    except SQLAlchemyError as exc:
        state = getattr(getattr(exc, "orig", None), "sqlstate", None)
        hints = {
            "25006": "Writes are blocked by the read-only transaction.",
            "42501": "The reader can query raw tables and analytics views only.",
            "42601": "Check the SQL syntax; submit one SELECT or WITH query.",
            "42P01": "The requested table or view does not exist; qualify it with raw. or analytics.",
            "42703": "The requested column does not exist.",
            "57014": "The query exceeded the 10-second timeout or was canceled.",
        }
        hint = hints.get(state, "Check the connection settings and query.")
        suffix = f" (SQLSTATE {state})" if state else ""
        print(f"Query failed{suffix}. {hint}", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
