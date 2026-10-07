from hashlib import sha256
from sqlalchemy import select, delete
from sqlalchemy.dialects.postgresql import insert
from olist_agent.analytics.metrics import METRICS
from olist_agent.db.schema import SPECS, documents, imports
from olist_agent.analytics.schema_context import schema_context


def source_documents(import_reports=None, coverage=None):
    docs = []
    for m in METRICS.values():
        docs.append({"document_id": m.id, "title": m.title, "section": "metric", "source": m.source,
                     "version": "1", "text": m.model_dump_json()})
    for name, (filename, keys, fields) in SPECS.items():
        observed = [r["files"][name]["columns"] for r in (import_reports or []) if name in r["files"]]
        observed = [list(header) for header in sorted({tuple(header) for header in observed})]
        docs.append({"document_id": f"schema:{name}", "title": f"Schema: {name}", "section": "schema",
            "source": filename + ("; observed import header" if observed else "; provisional contract, source file not inspected"),
            "version": "1", "text": f"raw.{name}: {fields}. Repeatable key: {keys}. Observed headers: {observed}. Query tools use approved analytics views, not raw tables."})
    docs.append({"document_id": "joins", "title": "Approved join paths", "section": "relationships", "source": "db/views.sql; project decisions", "version": "1",
        "text": "analytics.order_facts is one row per order; items and payments are independently aggregated by order_id before joining. category_items has one row per order item and joins products for category; payment_records has one row per payment record. Do not join item and payment records directly: totals multiply. customer_id identifies an order's customer record; customer_unique_id identifies repeat customers. Reviews and geolocation are not exposed to analytical tools."})
    docs.append({"document_id": "limits", "title": "Dataset limitations and date coverage", "section": "limitations", "source": "Kaggle Olist description; project decisions; imported coverage", "version": "1",
        "text": f"Historical Brazilian e-commerce data, original BRL. Observed coverage: {coverage or 'not yet imported'}. Source timezone unconfirmed; timestamps retained without conversion. Boundary months may be incomplete; different period lengths must be disclosed. No confirmed refunds, costs/profit, traffic, marketing, device or causal evidence. Never claim a cause from contributions. Zero baseline percentage changes are undefined. Source kinds: {sorted({r['source_kind'] for r in (import_reports or [])})}. Fixtures are not Olist population evidence."})
    docs.append({"document_id": "examples", "title": "Structured query examples", "section": "examples", "source": "Project tested query service", "version": "1",
        "text": "Gemini writes complete PostgreSQL SELECT SQL in execute_analytics_query.spec.sql; Python validates and executes the exact approved SQL. "
        "Monthly orders: SELECT date_trunc('month',order_purchase_timestamp)::date AS month, COUNT(DISTINCT order_id) AS value "
        "FROM analytics.order_facts WHERE order_purchase_timestamp >= '2017-01-01' AND order_purchase_timestamp < '2018-01-01' GROUP BY 1 ORDER BY month. "
        "Category sales use SUM(price) FROM analytics.category_items, grouped by category. Payment records use analytics.payment_records. "
        "End dates are exclusive. Chart-only follow-ups reuse previous approved results; changed dates or filters need new SQL approval."})
    for name, definition in schema_context()["views"].items():
        docs.append({"document_id": f"schema:{name}", "title": f"Approved view: {name}", "section": "schema",
            "source": "db/views.sql; validated imported column contract", "version": "2",
            "text": f"{name}: {definition}. This is an approved SQL relation; use explicit analytics qualification."})
    return docs


class LocalEmbeddings:
    def __init__(self, model_id):
        from sentence_transformers import SentenceTransformer
        self.model_id = model_id
        self.model = SentenceTransformer(model_id)
        if self.model.get_embedding_dimension() != 384:
            raise ValueError("This database requires 384-dimensional embeddings; explicit schema migration needed")

    def encode(self, texts):
        return self.model.encode(texts, normalize_embeddings=True).tolist()


def index_documents(db, embeddings, coverage):
    with db.connect() as conn:
        reports = conn.execute(select(imports.c.report)).scalars().all()
    docs = source_documents(reports, coverage)
    chunks = []
    for doc in docs:
        # Small source documents; bounded chunks avoid silently clipping model token limits.
        words = doc["text"].split()
        for offset in range(0, len(words), 150):
            chunk = dict(doc, text=" ".join(words[offset:offset + 180]))
            chunk["chunk_id"] = sha256(f"{doc['document_id']}:{offset}".encode()).hexdigest()
            chunk["content_hash"] = sha256(chunk["text"].encode()).hexdigest()
            chunks.append(chunk)
    with db.connect() as conn:
        existing = {r.chunk_id: (r.content_hash, r.model) for r in conn.execute(select(documents.c.chunk_id, documents.c.content_hash, documents.c.model))}
    changed = [c for c in chunks if existing.get(c["chunk_id"]) != (c["content_hash"], embeddings.model_id)]
    vectors = embeddings.encode([c["text"] for c in changed]) if changed else []
    with db.begin() as conn:
        for chunk, vector in zip(changed, vectors):
            values = dict(chunk, model=embeddings.model_id, embedding=vector)
            conn.execute(insert(documents).values(**values).on_conflict_do_update(index_elements=[documents.c.chunk_id], set_=values))
        # Only project-owned corpus; remove stale chunks and mixed embedding models atomically.
        conn.execute(delete(documents).where(documents.c.chunk_id.not_in([c["chunk_id"] for c in chunks])))
    return {"chunks": len(chunks), "embedded": len(changed), "model": embeddings.model_id, "dimension": 384}


class Retriever:
    def __init__(self, db, embeddings):
        self.db, self.embeddings = db, embeddings

    def retrieve(self, query, limit=5):
        if not query.strip() or len(query) > 2000 or not 1 <= limit <= 10:
            raise ValueError("Query must be 1–2000 characters; limit 1–10")
        vector = self.embeddings.encode([query])[0]
        distance = documents.c.embedding.cosine_distance(vector)
        with self.db.connect() as conn:
            rows = conn.execute(select(*[c for c in documents.c if c.name != "embedding"], distance.label("distance"))
                .where(documents.c.model == self.embeddings.model_id).order_by(distance).limit(limit)).mappings().all()
        if not rows:
            raise ValueError("No documents for this embedding model. Run olist index.")
        return [dict(r) for r in rows]
