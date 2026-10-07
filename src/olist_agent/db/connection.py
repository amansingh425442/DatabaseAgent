from contextlib import contextmanager
from sqlalchemy import create_engine, text


def engine(url):
    if not url:
        raise ValueError("Database URL is missing. Generate .env and initialize PostgreSQL first.")
    return create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 30})


@contextmanager
def readonly(db, timeout_ms=10000):
    with db.connect() as conn, conn.begin():
        conn.execute(text("SET TRANSACTION READ ONLY"))
        conn.execute(text("SELECT set_config('statement_timeout', :timeout, true)"), {"timeout": str(timeout_ms)})
        yield conn
