"""Optional REAL local embedding smoke: may download public model weights."""
from olist_agent.config import Settings
from olist_agent.retrieval.documents import LocalEmbeddings

embedding = LocalEmbeddings(Settings.load().embedding_model)
vectors = embedding.encode(["Placed order count counts distinct order_id.", "Payments use payment records."])
assert len(vectors) == 2 and all(len(v) == 384 for v in vectors)
print(f"REAL local Sentence Transformers smoke passed: {embedding.model_id}, 2 x 384 dimensions.")
