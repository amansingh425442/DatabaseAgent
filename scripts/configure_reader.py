"""Provision a local human SQL reader without changing agent query permissions."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import secrets
import sys

from dotenv import dotenv_values
from psycopg import Error as PsycopgError
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError


ROOT = Path(__file__).resolve().parents[1]
READER_ROLE = "olist_reader"
SCHEMAS = ("raw", "analytics", "app", "knowledge")


def local_url(value: str | None, setting: str) -> URL:
    if not value:
        raise ValueError(f"{setting} is missing from .env.")
    try:
        url = make_url(value)
    except Exception:
        raise ValueError(f"{setting} is not a valid database URL.") from None
    if url.drivername != "postgresql+psycopg":
        raise ValueError(f"{setting} must use the postgresql+psycopg driver.")
    if url.host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError(f"{setting} must point to PostgreSQL on this computer.")
    if not url.username or not url.password or not url.database:
        raise ValueError(f"{setting} must include a username, password, and database.")
    return url


def reader_settings(values: dict) -> tuple[URL, URL, str]:
    admin = local_url(values.get("ADMIN_DATABASE_URL"), "ADMIN_DATABASE_URL")
    existing = values.get("READER_DATABASE_URL")
    if existing:
        reader = local_url(existing, "READER_DATABASE_URL")
        if reader.username != READER_ROLE:
            raise ValueError("READER_DATABASE_URL must use the dedicated olist_reader role.")
        # Both accepted loopback names refer to this computer; port and database
        # must agree so a stale reader URL cannot target a different database.
        if (reader.port or 5432, reader.database) != (admin.port or 5432, admin.database):
            raise ValueError("READER_DATABASE_URL and ADMIN_DATABASE_URL target different databases.")
        password = reader.password
        if values.get("READER_PASSWORD") not in (None, "", password):
            raise ValueError("READER_PASSWORD conflicts with READER_DATABASE_URL in .env.")
    else:
        password = values.get("READER_PASSWORD") or secrets.token_urlsafe(32)
        reader = admin.set(username=READER_ROLE, password=password)
    return admin, reader, password


def update_env(path: Path, updates: dict[str, str]) -> None:
    """Replace only supplied keys and preserve other lines, comments, and endings."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        original = handle.read()
    newline = "\r\n" if "\r\n" in original else "\n"
    seen: set[str] = set()
    output: list[str] = []
    for line in original.splitlines(keepends=True):
        match = re.match(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match and match.group(1) in updates:
            key = match.group(1)
            ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            # Quote through dotenv's supported single-quoted format. Generated
            # passwords are URL safe, but an existing password may contain quotes.
            value = updates[key].replace("\\", "\\\\").replace("'", "\\'")
            output.append(f"{key}='{value}'{ending}")
            seen.add(key)
        else:
            output.append(line)
    for key, value in updates.items():
        if key not in seen:
            if output and not output[-1].endswith(("\n", "\r")):
                output[-1] += newline
            escaped = value.replace("\\", "\\\\").replace("'", "\\'")
            output.append(f"{key}='{escaped}'{newline}")
    updated = "".join(output)
    if updated != original:
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(updated)


def configure_reader(admin_url: URL, password: str) -> None:
    engine = create_engine(admin_url, connect_args={"connect_timeout": 30})
    try:
        with engine.begin() as conn:
            available = set(conn.execute(text("SELECT nspname FROM pg_namespace")).scalars())
            if not set(SCHEMAS).issubset(available):
                raise ValueError("Initialize the Olist database before configuring reader access.")
            role = sql.Identifier(READER_ROLE)
            raw = conn.connection.driver_connection
            exists = conn.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": READER_ROLE}
            ).scalar()
            if not exists:
                raw.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(role, sql.Literal(password)))
            else:
                # Ownership cannot be removed with REVOKE. A dedicated reader
                # must not own databases, functions, or other database objects.
                owns_objects = conn.execute(text("""
                    SELECT EXISTS (
                      SELECT 1 FROM pg_shdepend dependency
                      JOIN pg_roles r ON r.oid = dependency.refobjid
                      WHERE dependency.refclassid = 'pg_authid'::regclass
                        AND dependency.deptype = 'o' AND r.rolname = :role
                    )
                """), {"role": READER_ROLE}).scalar()
                if owns_objects:
                    raise ValueError("olist_reader owns database objects; use a dedicated reader role.")
                raw.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(role, sql.Literal(password)))
            raw.execute(sql.SQL(
                "ALTER ROLE {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT"
            ).format(role))
            memberships = conn.execute(text("""
                SELECT granted.rolname FROM pg_auth_members membership
                JOIN pg_roles granted ON granted.oid = membership.roleid
                JOIN pg_roles member ON member.oid = membership.member
                WHERE member.rolname = :role
            """), {"role": READER_ROLE}).scalars().all()
            for membership in memberships:
                raw.execute(sql.SQL("REVOKE {} FROM {}").format(sql.Identifier(membership), role))
            owner = sql.Identifier(conn.execute(text("SELECT current_user")).scalar_one())
            for name in SCHEMAS:
                schema = sql.Identifier(name)
                raw.execute(sql.SQL("REVOKE ALL ON SCHEMA {} FROM {}").format(schema, role))
                raw.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM {}").format(schema, role))
                raw.execute(sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA {} FROM {}").format(schema, role))
                raw.execute(sql.SQL(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} REVOKE ALL ON TABLES FROM {}"
                ).format(owner, schema, role))
                if name in ("raw", "analytics"):
                    raw.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(schema, role))
                    raw.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(schema, role))
                    raw.execute(sql.SQL(
                        "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} GRANT SELECT ON TABLES TO {}"
                    ).format(owner, schema, role))
            database = sql.Identifier(conn.execute(text("SELECT current_database()")).scalar_one())
            raw.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(database, role))
            raw.execute(sql.SQL("REVOKE CREATE ON DATABASE {} FROM {}").format(database, role))
            raw.execute(sql.SQL("ALTER ROLE {} SET search_path = analytics, raw, pg_catalog").format(role))
            raw.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(role))
            raw.execute(sql.SQL("ALTER ROLE {} SET statement_timeout = '10s'").format(role))
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    try:
        if not args.env_file.is_file():
            raise ValueError("The .env file is missing; configure the local database first.")
        admin, reader, password = reader_settings(dict(dotenv_values(args.env_file, interpolate=False)))
        configure_reader(admin, password)
        update_env(args.env_file, {
            "READER_PASSWORD": password,
            "READER_DATABASE_URL": reader.render_as_string(hide_password=False),
        })
    except (ValueError, OSError) as exc:
        print(f"Reader setup failed: {exc}", file=sys.stderr)
        return 1
    except (SQLAlchemyError, PsycopgError) as exc:
        state = getattr(getattr(exc, "orig", exc), "sqlstate", None)
        suffix = f" (SQLSTATE {state})" if state else ""
        print(f"Reader setup failed: database connection or permission error{suffix}. "
              "Check that PostgreSQL is running and ADMIN_DATABASE_URL is configured.", file=sys.stderr)
        return 1
    print("Configured olist_reader with read-only access to raw and analytics.")
    print("Reader credentials are stored in .env; no credentials were printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
