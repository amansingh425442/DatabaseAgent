from datetime import datetime
from uuid import uuid4
from sqlalchemy import insert, select, text
from olist_agent.db.schema import results, investigations, imports
from olist_agent.models import QueryResult


class Store:
    def __init__(self, db):
        self.db = db

    def _session(self, conn, session_id):
        # Database RLS enforces this session capability in addition to query predicates.
        conn.execute(text("SELECT set_config('app.session_id', :id, true)"), {"id": session_id})

    def save_result(self, session_id, result):
        with self.db.begin() as conn:
            self._session(conn, session_id)
            conn.execute(insert(results).values(result_id=result.result_id, session_id=session_id,
                payload=result.model_dump(mode="json"), created_at=datetime.now()))

    def get_result(self, session_id, result_id):
        with self.db.begin() as conn:
            self._session(conn, session_id)
            payload = conn.execute(select(results.c.payload).where(results.c.session_id == session_id,
                                       results.c.result_id == result_id)).scalar_one_or_none()
        if payload is None:
            raise ValueError("Result does not exist in this session")
        return QueryResult.model_validate(payload)

    def save_report(self, session_id, question, report, messages, metadata=None):
        with self.db.begin() as conn:
            self._session(conn, session_id)
            conn.execute(insert(investigations).values(id=str(uuid4()), session_id=session_id,
                question=question, report=dict(report.model_dump(mode="json"), **(metadata or {})), messages=messages,
                created_at=datetime.now()))

    def history(self, session_id):
        with self.db.begin() as conn:
            self._session(conn, session_id)
            rows = conn.execute(select(investigations).where(investigations.c.session_id == session_id)
                                .order_by(investigations.c.created_at.desc()).limit(50)).mappings().all()
        return [dict(row) for row in rows]

    def provenance(self):
        with self.db.connect() as conn:
            reports = conn.execute(select(imports.c.report)).scalars().all()
        kinds = sorted({r["source_kind"] for r in reports})
        return f"PostgreSQL approved views; source kinds: {', '.join(kinds) or 'no import recorded'}"


class MemoryStore:
    """Explicit test double, never selected by the application."""
    def __init__(self):
        self.results = {}
        self.reports = []

    def save_result(self, session_id, result):
        self.results[session_id, result.result_id] = result

    def get_result(self, session_id, result_id):
        try:
            return self.results[session_id, result_id]
        except KeyError:
            raise ValueError("Result does not exist in this session") from None

    def save_report(self, *args):
        self.reports.append(args)

    def provenance(self):
        return "TEST FIXTURE, not the full Olist dataset"
