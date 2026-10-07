"""Approved model SQL execution; legacy templates remain for CLI/fixture analyses."""
from datetime import date, datetime
from decimal import Decimal
import json
from sqlalchemy import text, bindparam, Integer, String
from sqlalchemy.dialects import postgresql
from olist_agent.db.connection import readonly
from olist_agent.models import QuerySpec, SQLQuerySpec, QueryResult
from .sql_validation import preview_sql, validate_sql
from .schema_context import APPROVED_COLUMNS, schema_context
from .metrics import get_definition

EXPRESSIONS = {
    "placed_orders": "COUNT(DISTINCT order_id)",
    "delivered_orders": "COUNT(DISTINCT order_id) FILTER (WHERE order_status='delivered')",
    "item_sales": "COALESCE(SUM(item_sales_value),0)",
    "freight": "COALESCE(SUM(freight_value),0)",
    "recorded_payments": "COALESCE(SUM(recorded_payment_value),0)",
    "aov": "COALESCE(SUM(item_sales_value),0)/NULLIF(COUNT(DISTINCT order_id),0)",
    "delivery_days": "AVG(delivery_days)",
    "late_delivery_rate": "100.0*COUNT(*) FILTER (WHERE is_late)/NULLIF(COUNT(is_late),0)",
    "category_sales": "COALESCE(SUM(price),0)",
}
GROUPS = {"total": [], "month": ["date_trunc('month',order_purchase_timestamp)::date AS month"],
          "year": ["EXTRACT(YEAR FROM order_purchase_timestamp)::int AS year"],
          "state": ["COALESCE(customer_state,'Unknown') AS state"],
          "category": ["category"],
          "category_month": ["date_trunc('month',order_purchase_timestamp)::date AS month", "category"],
          "payment_method": ["COALESCE(payment_type,'Unknown') AS payment_method"]}


def compile_query(spec: QuerySpec, max_rows=500):
    if not isinstance(spec, QuerySpec):
        raise ValueError("Only validated QuerySpec objects are accepted; arbitrary SQL is disabled")
    params = {"row_limit": max_rows + 1}
    predicates = []
    for name, op in (("start", ">="), ("end", "<")):
        if getattr(spec, name):
            predicates.append(f"order_purchase_timestamp {op} :{name}")
            params[name] = getattr(spec, name)
    bindings = []
    for name, column, value_type in (("statuses", "order_status", String()),
                                    ("states", "customer_state", String()),
                                    ("years", "EXTRACT(YEAR FROM order_purchase_timestamp)::int", Integer())):
        if getattr(spec, name):
            predicates.append(f"{column} IN :{name}")
            params[name] = getattr(spec, name)
            bindings.append(bindparam(name, expanding=True, type_=value_type))
    where = " WHERE " + " AND ".join(predicates) if predicates else ""
    groups = GROUPS[spec.group_by]
    group = " GROUP BY " + ",".join(str(i + 1) for i in range(len(groups))) if groups else ""
    order = " ORDER BY " + ("month, value DESC" if spec.group_by == "category_month" else spec.group_by if spec.group_by in ("month", "year") else "value DESC, 1") if groups else ""
    if spec.metric.startswith("payment_"):
        expression = {"payment_record_share": "COUNT(*)",
                      "payment_value_share": "COALESCE(SUM(payment_value),0)",
                      "payment_order_share": "COUNT(DISTINCT order_id)"}[spec.metric]
        denominator = "(SELECT COUNT(DISTINCT order_id) FROM filtered)" if spec.metric == "payment_order_share" else "SUM(amount) OVER ()"
        sql = f"WITH filtered AS (SELECT * FROM analytics.payment_records{where}), grouped AS (SELECT {groups[0]}, {expression} AS amount, COUNT(*) FILTER (WHERE payment_value IS NULL) AS missing_values FROM filtered GROUP BY 1) SELECT payment_method, amount, 100.0*amount/NULLIF({denominator},0) AS value, missing_values FROM grouped{order} LIMIT :row_limit"
    else:
        relation = "category_items" if spec.metric == "category_sales" else "order_facts"
        missing = {"item_sales": "COALESCE(SUM(missing_item_prices),0)",
                   "aov": "COALESCE(SUM(missing_item_prices),0)",
                   "freight": "COALESCE(SUM(missing_freight_values),0)",
                   "recorded_payments": "COALESCE(SUM(missing_payment_values),0)",
                   "category_sales": "COUNT(*) FILTER (WHERE price IS NULL)"}.get(spec.metric, "0")
        sql = f"SELECT {', '.join(groups + [EXPRESSIONS[spec.metric] + ' AS value', missing + ' AS missing_values'])} FROM analytics.{relation}{where}{group}{order} LIMIT :row_limit"
    return text(sql).bindparams(*bindings), params


def serializable(value):
    if isinstance(value, Decimal):
        return str(value)  # Preserve exact numbers in stored evidence.
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def preview_query(spec, max_rows=500):
    """Compile the exact bound statement for review without contacting a database."""
    if isinstance(spec, SQLQuerySpec):
        return preview_sql(spec, max_rows)
    query, params = compile_query(spec, max_rows)
    compiled = query.bindparams(**params).compile(
        dialect=postgresql.dialect(paramstyle="named"),
        compile_kwargs={"render_postcompile": True},
    )
    return {"spec": spec.model_dump(mode="json"), "query": str(compiled),
            "display_query": str(query.bindparams(**params).compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})),
            "parameters": {key: serializable(value) for key, value in compiled.params.items()}}


def bound_payload(result, max_bytes):
    while len(result.model_dump_json().encode("utf-8")) > max_bytes and result.rows:
        result.rows.pop()
        result.truncated = True
        warning = "Result truncated: displayed rows are not the full population."
        if warning not in result.warnings:
            result.warnings.append(warning)
    if len(result.model_dump_json().encode("utf-8")) > max_bytes:
        raise ValueError("Result metadata exceeds configured payload limit")
    return result


class QueryService:
    def __init__(self, db, settings, store):
        self.db, self.settings, self.store = db, settings, store
        self._schema_columns = None

    def schema_context(self):
        """Read actual column metadata, not business rows; cache per service."""
        if self._schema_columns is None:
            with readonly(self.db, self.settings.statement_timeout_ms) as conn:
                records = conn.execute(text("SELECT table_name,column_name,data_type "
                    "FROM information_schema.columns WHERE table_schema='analytics' "
                    "ORDER BY table_name,ordinal_position")).mappings().all()
            columns = {name: {} for name in APPROVED_COLUMNS}
            for row in records:
                if row["table_name"] in columns:
                    columns[row["table_name"]][row["column_name"]] = row["data_type"]
            if any(not fields for fields in columns.values()):
                raise ValueError("Approved analytics schema is unavailable; initialize the database")
            self._schema_columns = columns
        return schema_context(self._schema_columns)

    def execute(self, spec, session_id):
        if isinstance(spec, SQLQuerySpec):
            return self.execute_sql(spec, session_id)
        query, params = compile_query(spec, self.settings.max_rows)
        with readonly(self.db, self.settings.statement_timeout_ms) as conn:
            cursor = conn.execute(query, params)
            columns = list(cursor.keys())
            raw_rows = cursor.fetchall()
        truncated = len(raw_rows) > self.settings.max_rows
        raw_rows = raw_rows[:self.settings.max_rows]
        types = {c: next((type(row[n]).__name__ for row in raw_rows if row[n] is not None), "unknown")
                 for n, c in enumerate(columns)}
        rows, size = [], 0
        for row in raw_rows:
            converted = {c: serializable(v) for c, v in zip(columns, row)}
            size += len(json.dumps(converted).encode())
            if size > self.settings.max_payload_bytes:
                truncated = True
                break
            rows.append(converted)
        warnings = ["Historical data in BRL; timestamp timezone is unconfirmed."]
        if truncated:
            warnings.append("Result truncated: displayed rows are not the full population.")
        if any(Decimal(str(row.get("missing_values", 0))) > 0 for row in rows):
            warnings.append("Missing numeric values were excluded from sums; see missing_values column.")
        if spec.metric == "payment_order_share":
            warnings.append("Orders can use multiple methods; shares may sum above 100%.")
        warnings.append("Boundary months may be incomplete; compare equal date ranges.")
        preview = preview_query(spec, self.settings.max_rows)
        result = QueryResult(columns=columns, types=types, rows=rows, metric_id=spec.metric,
                             spec=spec, query=preview["query"], parameters=preview["parameters"],
                             truncated=truncated, warnings=warnings, provenance=self.store.provenance())
        bound_payload(result, self.settings.max_payload_bytes)
        self.store.save_result(session_id, result)
        return result

    def execute_sql(self, spec, session_id):
        validate_sql(spec.sql, self._schema_columns)
        with readonly(self.db, self.settings.statement_timeout_ms) as conn:
            # Prevent unqualified function/type lookup in user-controlled schemas.
            conn.exec_driver_sql("SET LOCAL search_path = pg_catalog")
            with conn.execution_options(stream_results=True).exec_driver_sql(
                    spec.sql, execution_options={"no_parameters": True}) as cursor:
                columns = list(cursor.keys())
                if len(columns) != len(set(columns)):
                    raise ValueError("SQL output columns need unique aliases")
                raw_rows = cursor.fetchmany(self.settings.max_rows + 1)
        truncated = len(raw_rows) > self.settings.max_rows
        raw_rows = raw_rows[:self.settings.max_rows]
        types = {column: next((type(row[index]).__name__ for row in raw_rows if row[index] is not None), "unknown")
                 for index, column in enumerate(columns)}
        rows = [{column: serializable(value) for column, value in zip(columns, row)} for row in raw_rows]
        warnings = ["Historical data in BRL; source timestamp timezone is unconfirmed."]
        if truncated:
            warnings.append("Result truncated: displayed rows are not the full population.")
        result = QueryResult(columns=columns, types=types, rows=rows, metric_id=spec.metric,
            spec=spec, query=spec.sql, parameters={}, truncated=truncated, warnings=warnings,
            provenance=self.store.provenance())
        bound_payload(result, self.settings.max_payload_bytes)
        self.store.save_result(session_id, result)
        return result

    def coverage(self):
        with readonly(self.db, self.settings.statement_timeout_ms) as conn:
            row = conn.execute(text("SELECT MIN(order_purchase_timestamp) AS start, MAX(order_purchase_timestamp) AS end, COUNT(*) AS orders FROM analytics.order_facts")).mappings().one()
        return {k: serializable(v) for k, v in row.items()}


def check_result(result):
    from .presentation import value_column
    column = value_column(result)
    values = [Decimal(str(r[column])) for r in result.rows if column and r.get(column) is not None]
    additive = result.metric_id in ("placed_orders", "delivered_orders", "item_sales", "freight", "recorded_payments", "category_sales", "payment_record_share", "payment_value_share")
    return {"result_id": result.result_id, "rows": len(result.rows), "null_values": len(result.rows) - len(values),
            "sum_of_displayed_values": str(sum(values)) if additive else None,
            "truncated": result.truncated, "warnings": result.warnings,
            "verification": "Arithmetic over stored result only; use a separate total query to reconcile grouped results."}


def percent_change(before, after):
    before, after = Decimal(str(before)), Decimal(str(after))
    return None if before == 0 else (after - before) / before * 100
