import os
from importlib.resources import files
from sqlalchemy import text
from psycopg import sql
from .schema import metadata


def initialize(db):
    with db.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        for name in ("raw", "analytics", "knowledge", "app"):
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {name}"))
    metadata.create_all(db)
    with db.begin() as conn:
        for statement in files("olist_agent.db").joinpath("views.sql").read_text().split(";"):
            if statement.strip():
                conn.execute(text(statement))
        raw = conn.connection.driver_connection
        for role, env in (("olist_analytics", "ANALYTICS_PASSWORD"), ("olist_app", "APP_PASSWORD")):
            password = os.environ.get(env)
            if not password:
                raise ValueError(f"Missing {env}")
            if not conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role}).scalar():
                raw.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
            else:
                raw.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
            raw.execute(sql.SQL("ALTER ROLE {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT").format(sql.Identifier(role)))
            for schema in ("raw", "analytics", "knowledge", "app"):
                raw.execute(sql.SQL("REVOKE ALL ON SCHEMA {} FROM {}").format(sql.Identifier(schema), sql.Identifier(role)))
                raw.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM {}").format(sql.Identifier(schema), sql.Identifier(role)))
        conn.execute(text("REVOKE CREATE ON SCHEMA public FROM PUBLIC"))
        conn.execute(text("GRANT USAGE ON SCHEMA analytics TO olist_analytics"))
        conn.execute(text("GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO olist_analytics"))
        conn.execute(text("ALTER ROLE olist_analytics SET default_transaction_read_only = on"))
        conn.execute(text("GRANT USAGE ON SCHEMA app, knowledge TO olist_app"))
        conn.execute(text("GRANT SELECT, INSERT ON app.results, app.investigations TO olist_app"))
        conn.execute(text("GRANT SELECT ON app.imports, knowledge.documents TO olist_app"))
        for table in ("results", "investigations"):
            conn.execute(text(f"ALTER TABLE app.{table} ENABLE ROW LEVEL SECURITY"))
            conn.execute(text(f"DROP POLICY IF EXISTS session_scope ON app.{table}"))
            conn.execute(text(f"CREATE POLICY session_scope ON app.{table} TO olist_app USING (session_id = current_setting('app.session_id', true)) WITH CHECK (session_id = current_setting('app.session_id', true))"))
