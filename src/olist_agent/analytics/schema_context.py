"""Column-level context for the views accessible to generated SQL."""
from olist_agent.analytics.metrics import METRICS
from olist_agent.db.schema import SPECS

_TYPES = {"id": "text", "text": "text", "date": "timestamp", "money": "numeric",
          "coordinate": "numeric", "int": "integer"}
APPROVED_COLUMNS = {
    "order_facts": {name: _TYPES[kind] for name, kind in SPECS["orders"][2].items()},
    "category_items": {"order_id": "text", "order_item_id": "integer", "price": "numeric",
        "freight_value": "numeric", "order_purchase_timestamp": "timestamp", "order_status": "text",
        "customer_state": "text", "category": "text"},
    "payment_records": {name: _TYPES[kind] for name, kind in SPECS["order_payments"][2].items()},
}
APPROVED_COLUMNS["order_facts"].update({"customer_unique_id": "text", "customer_state": "text",
    "item_sales_value": "numeric", "freight_value": "numeric", "item_count": "bigint",
    "missing_item_prices": "bigint", "missing_freight_values": "bigint",
    "recorded_payment_value": "numeric", "payment_count": "bigint", "missing_payment_values": "bigint",
    "delivery_days": "numeric", "is_late": "boolean"})
APPROVED_COLUMNS["payment_records"].update({"order_purchase_timestamp": "timestamp",
    "order_status": "text", "customer_state": "text"})


def schema_context(columns=None):
    columns = columns or APPROVED_COLUMNS
    grains = {"order_facts": "One row per order; item and payment amounts already aggregated separately.",
              "category_items": "One row per order item. Price is item sales, not whole-order payments.",
              "payment_records": "One row per payment record; an order can have several records."}
    return {"dialect": "PostgreSQL", "views": {f"analytics.{name}": {
        "grain": grains[name], "columns": fields} for name, fields in columns.items()},
        "relationships": ["order_facts.order_id = category_items.order_id (one-to-many)",
            "order_facts.order_id = payment_records.order_id (one-to-many)",
            "Aggregate item/payment records by order_id before joining to avoid multiplying totals."],
        "metrics": {name: definition.model_dump() for name, definition in METRICS.items()},
        "limitations": "Historical BRL; purchase coverage September 2016–October 2018, incomplete boundary years. "
            "Source timezone unconfirmed. Profit, refunds, traffic and causal explanations are unavailable."}
