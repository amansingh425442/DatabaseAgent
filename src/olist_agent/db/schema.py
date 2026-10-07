"""Provisional CSV contract; importer inspects actual headers before writing."""
from sqlalchemy import (MetaData, Table, Column, String, Integer, Numeric, DateTime,
                        ForeignKey, CheckConstraint, Index, Text)
from sqlalchemy.dialects.postgresql import JSONB
from pgvector.sqlalchemy import Vector

metadata = MetaData()
SPECS = {
    "customers": ("olist_customers_dataset.csv", "customer_id", {
        "customer_id": "id", "customer_unique_id": "id", "customer_zip_code_prefix": "id",
        "customer_city": "text", "customer_state": "text"}),
    "sellers": ("olist_sellers_dataset.csv", "seller_id", {
        "seller_id": "id", "seller_zip_code_prefix": "id", "seller_city": "text", "seller_state": "text"}),
    "category_translation": ("product_category_name_translation.csv", "product_category_name", {
        "product_category_name": "text", "product_category_name_english": "text"}),
    "products": ("olist_products_dataset.csv", "product_id", {
        "product_id": "id", "product_category_name": "text", "product_name_lenght": "int",
        "product_description_lenght": "int", "product_photos_qty": "int", "product_weight_g": "int",
        "product_length_cm": "int", "product_height_cm": "int", "product_width_cm": "int"}),
    "orders": ("olist_orders_dataset.csv", "order_id", {
        "order_id": "id", "customer_id": "id", "order_status": "text",
        "order_purchase_timestamp": "date", "order_approved_at": "date",
        "order_delivered_carrier_date": "date", "order_delivered_customer_date": "date",
        "order_estimated_delivery_date": "date"}),
    "order_items": ("olist_order_items_dataset.csv", ("order_id", "order_item_id"), {
        "order_id": "id", "order_item_id": "int", "product_id": "id", "seller_id": "id",
        "shipping_limit_date": "date", "price": "money", "freight_value": "money"}),
    "order_payments": ("olist_order_payments_dataset.csv", ("order_id", "payment_sequential"), {
        "order_id": "id", "payment_sequential": "int", "payment_type": "text",
        "payment_installments": "int", "payment_value": "money"}),
    "order_reviews": ("olist_order_reviews_dataset.csv", "source_key", {
        "review_id": "id", "order_id": "id", "review_score": "int",
        "review_comment_title": "text", "review_comment_message": "text",
        "review_creation_date": "date", "review_answer_timestamp": "date"}),
    "geolocation": ("olist_geolocation_dataset.csv", "source_key", {
        "geolocation_zip_code_prefix": "id", "geolocation_lat": "coordinate",
        "geolocation_lng": "coordinate", "geolocation_city": "text", "geolocation_state": "text"}),
}
TYPES = {"id": String(64), "text": Text(), "int": Integer(), "money": Numeric(18, 2),
         "date": DateTime(timezone=False), "coordinate": Numeric()}
FKS = {("orders", "customer_id"): "raw.customers.customer_id",
       ("order_items", "order_id"): "raw.orders.order_id",
       ("order_items", "product_id"): "raw.products.product_id",
       ("order_items", "seller_id"): "raw.sellers.seller_id",
       ("order_payments", "order_id"): "raw.orders.order_id",
       ("order_reviews", "order_id"): "raw.orders.order_id"}
tables = {}
for name, (_, keys, fields) in SPECS.items():
    keys = (keys,) if isinstance(keys, str) else keys
    cols = []
    for field, kind in fields.items():
        fk = FKS.get((name, field))
        args = [ForeignKey(fk)] if fk else []
        required = field in keys or fk or (name == "orders" and field in ("order_status", "order_purchase_timestamp"))
        cols.append(Column(field, TYPES[kind], *args, primary_key=field in keys, nullable=not required))
    if keys == ("source_key",):
        cols.append(Column("source_key", String(80), primary_key=True))
    constraints = [CheckConstraint(f"{f} >= 0", name=f"{name}_{f}_nonnegative")
                   for f, k in fields.items() if k in ("money", "int")]
    if name == "order_reviews":
        constraints.append(CheckConstraint("review_score BETWEEN 1 AND 5"))
    if name == "geolocation":
        constraints.extend([CheckConstraint("geolocation_lat BETWEEN -90 AND 90"),
                            CheckConstraint("geolocation_lng BETWEEN -180 AND 180")])
    tables[name] = Table(name, metadata, *cols, *constraints, schema="raw")
for table, column in (("orders", "order_purchase_timestamp"), ("orders", "customer_id"),
                      ("order_items", "product_id"), ("order_items", "seller_id"),
                      ("order_reviews", "order_id"), ("geolocation", "geolocation_zip_code_prefix")):
    Index(f"ix_{table}_{column}", tables[table].c[column])

documents = Table("documents", metadata,
    Column("chunk_id", String(64), primary_key=True), Column("document_id", Text, nullable=False),
    Column("title", Text, nullable=False), Column("section", Text, nullable=False),
    Column("source", Text, nullable=False), Column("version", Text, nullable=False),
    Column("text", Text, nullable=False), Column("model", Text, nullable=False),
    Column("content_hash", String(64), nullable=False), Column("embedding", Vector(384), nullable=False),
    schema="knowledge")
results = Table("results", metadata,
    Column("result_id", String(36), primary_key=True), Column("session_id", String(36), nullable=False),
    Column("payload", JSONB, nullable=False), Column("created_at", DateTime, nullable=False), schema="app")
investigations = Table("investigations", metadata,
    Column("id", String(36), primary_key=True), Column("session_id", String(36), nullable=False),
    Column("question", Text, nullable=False), Column("report", JSONB, nullable=False),
    Column("messages", JSONB, nullable=False), Column("created_at", DateTime, nullable=False), schema="app")
imports = Table("imports", metadata,
    Column("id", String(36), primary_key=True), Column("report", JSONB, nullable=False),
    Column("created_at", DateTime, nullable=False), schema="app")
Index("ix_results_session", results.c.session_id)
Index("ix_investigations_session", investigations.c.session_id)
