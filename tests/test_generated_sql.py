"""Model SQL validation, exact previews and generic chart results."""
import pytest
from olist_agent.analytics.sql_validation import validate_sql, preview_sql
from olist_agent.analytics.schema_context import schema_context
from olist_agent.analytics.presentation import make_result_report
from olist_agent.charts.render import build_chart
from olist_agent.models import SQLQuerySpec, QueryResult


YEAR_SQL = """SELECT EXTRACT(YEAR FROM order_purchase_timestamp)::int AS year,
COUNT(DISTINCT order_id) AS value FROM analytics.order_facts
WHERE EXTRACT(YEAR FROM order_purchase_timestamp) IN (2016, 2018)
GROUP BY 1 ORDER BY year"""


@pytest.mark.parametrize("sql", [
    YEAR_SQL,
    "SELECT COUNT(*) AS value FROM analytics.order_facts",
    "WITH annual AS (SELECT EXTRACT(YEAR FROM order_purchase_timestamp)::int AS year, "
        "SUM(item_sales_value) AS value FROM analytics.order_facts GROUP BY 1) "
        "SELECT year,value,LAG(value) OVER (ORDER BY year) AS previous_value FROM annual",
    "SELECT category,SUM(price) AS value FROM analytics.category_items "
        "WHERE category LIKE '%home%' GROUP BY category HAVING SUM(price)>100 ORDER BY value DESC LIMIT 10",
    "SELECT DATE_TRUNC('month',order_purchase_timestamp)::date AS month,COUNT(*) AS value "
        "FROM analytics.order_facts GROUP BY 1 ORDER BY month",
    "SELECT o.customer_state,COUNT(DISTINCT o.order_id) AS value "
        "FROM analytics.order_facts o JOIN analytics.category_items c ON o.order_id=c.order_id "
        "GROUP BY o.customer_state",
    "SELECT order_id FROM analytics.order_facts UNION SELECT order_id FROM analytics.category_items",
    'SELECT "order_status", COUNT(*) AS value FROM "analytics"."order_facts" GROUP BY 1',
])
def test_valid_generated_analytical_sql(sql):
    validate_sql(sql)
    spec = SQLQuerySpec(sql=sql)
    preview = preview_sql(spec)
    assert preview["query"] == preview["display_query"] == sql
    assert preview["parameters"] == {}


@pytest.mark.parametrize("sql", [
    "DELETE FROM analytics.order_facts",
    "SELECT COUNT(*) FROM analytics.order_facts; SELECT 1",
    "WITH gone AS (DELETE FROM raw.orders RETURNING *) SELECT * FROM gone",
    "SELECT * INTO public.stolen FROM analytics.order_facts",
    "SELECT * FROM analytics.order_facts FOR UPDATE",
    "SELECT * FROM raw.orders",
    "SELECT * FROM app.results",
    "SELECT * FROM knowledge.documents",
    "SELECT * FROM pg_catalog.pg_authid",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM order_facts",
    "SELECT pg_sleep(10) FROM analytics.order_facts",
    "SELECT set_config('app.session_id','other',false) FROM analytics.order_facts",
    "SELECT pg_read_file('secret') FROM analytics.order_facts",
    "SELECT pg_catalog.count(*) FROM analytics.order_facts",
    "SELECT * FROM analytics.order_facts WHERE customer_city='x'",
    "SELECT price FROM analytics.order_facts",
    "SELECT order_id FROM analytics.order_facts o JOIN analytics.category_items c USING(customer_id)",
    "SELECT order_id FROM analytics.order_facts o JOIN analytics.category_items c ON o.order_id=c.order_id",
    "WITH hidden AS (SELECT * FROM raw.orders) SELECT * FROM analytics.order_facts",
    "WITH nested AS (WITH pg_class AS (SELECT * FROM analytics.order_facts) SELECT * FROM pg_class) "
        "SELECT * FROM pg_class",
    "WITH RECURSIVE x AS (SELECT order_id FROM analytics.order_facts UNION ALL SELECT order_id FROM x) SELECT * FROM x",
    "SELECT order_id::regclass FROM analytics.order_facts",
    "COPY (SELECT * FROM analytics.order_facts) TO '/tmp/stolen'",
])
def test_reject_unsafe_or_out_of_scope_sql(sql):
    with pytest.raises(ValueError):
        validate_sql(sql)


def test_schema_context_has_columns_types_grains_and_definitions():
    context = schema_context()
    assert context["views"]["analytics.order_facts"]["columns"]["item_sales_value"] == "numeric"
    assert context["views"]["analytics.category_items"]["columns"]["price"] == "numeric"
    assert "customer_city" not in context["views"]["analytics.order_facts"]["columns"]
    assert context["metrics"]["placed_orders"]["formula"] == "COUNT(DISTINCT order_id)"


def test_custom_sql_output_has_grounded_summary_and_chart():
    spec = SQLQuerySpec(sql="SELECT order_status AS status,SUM(item_sales_value) AS sales "
        "FROM analytics.order_facts GROUP BY order_status", title="Sales by status", units="BRL", x="status", y="sales")
    result = QueryResult(columns=["status","sales"], types={"status":"str","sales":"Decimal"},
        rows=[{"status":"delivered","sales":"125.50"},{"status":"canceled","sales":"25.00"}],
        metric_id="custom", spec=spec, query=spec.sql, parameters={}, provenance="TEST FIXTURE")
    report = make_result_report("Sales by status with charts", [result], True)
    assert "125.50" in report.answer and "BRL" in report.answer
    assert report.tables == [result.result_id] and len(report.charts) == 1
    figure = build_chart(report.charts[0], result)
    assert list(figure.data[0].y) == [125.5, 25.0]
