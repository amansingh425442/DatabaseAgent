from datetime import datetime
from decimal import Decimal
from pathlib import Path
import pytest
from pydantic import ValidationError
from olist_agent.models import QuerySpec, QueryResult, ChartSpec
from olist_agent.analytics.query import compile_query, preview_query, percent_change, check_result, bound_payload
from olist_agent.analytics.metrics import METRICS
from olist_agent.ingestion.importer import convert, inspect_files
from olist_agent.db.schema import tables
from olist_agent.retrieval.documents import source_documents
from olist_agent.charts.render import build_chart
from olist_agent.services.store import MemoryStore
from olist_agent.agent.runtime import ToolBudget, BudgetExceeded, charts_allowed

FIXTURES = Path(__file__).parent / "fixtures" / "raw"


def sample(metric="item_sales", rows=None, group="month"):
    return QueryResult(columns=["month", "value"], types={"month":"date", "value":"Decimal"},
        rows=rows if rows is not None else [{"month":"2017-08-01", "value":"230.00"}, {"month":"2017-09-01", "value":"20.00"}],
        metric_id=metric, spec=QuerySpec(metric=metric, group_by=group), query="fixture", parameters={}, provenance="SYNTHETIC TEST FIXTURE")


def test_import_headers_and_identifiers():
    headers = inspect_files(FIXTURES)
    assert len(headers) == 9
    assert convert("00123", "id") == "00123"
    assert convert("100.10", "money") == Decimal("100.10")
    assert convert("-23.547981234567891", "coordinate") == Decimal("-23.547981234567891")
    assert convert("", "money") is None
    assert convert("2017-08-01 10:00:00", "date") == datetime(2017,8,1,10)
    assert not tables["order_reviews"].c.review_id.primary_key
    assert not tables["geolocation"].c.geolocation_zip_code_prefix.primary_key


@pytest.mark.parametrize("value,kind", [("NaN","money"),("-1","money"),("1.234","money"),("1.5","int"),("2017-02-30 00:00:00","date")])
def test_invalid_conversion(value, kind):
    with pytest.raises((ValueError, ArithmeticError)):
        convert(value,kind)


def test_incomplete_header_fails_before_writes(tmp_path):
    (tmp_path / "olist_customers_dataset.csv").write_text("customer_id\nc1\n")
    with pytest.raises(ValueError, match="missing columns"):
        inspect_files(tmp_path)


@pytest.mark.parametrize("payload", [
    {"metric":"profit"}, {"metric":"placed_orders", "sql":"DELETE FROM raw.orders"},
    {"metric":"placed_orders", "group_by":"raw.orders;DROP TABLE x"},
    {"metric":"placed_orders", "statuses":["delivered');SELECT pg_sleep(20)--"]},
    {"metric":"recorded_payments", "group_by":"category"},
    {"metric":"item_sales", "start":"2017-09-01", "end":"2017-08-01"},
    {"metric":"payment_value_share", "group_by":"total"},
    {"metric":"category_sales", "group_by":"year"},
    {"metric":"payment_record_share", "group_by":"year"},
    {"metric":"placed_orders", "years":[1899]},
    {"metric":"placed_orders", "years":[2101]},
    {"metric":"placed_orders", "years":list(range(2000, 2021))},
    {"metric":"placed_orders", "years":["2016); DROP TABLE raw.orders; --"]},
])
def test_disallowed_queries(payload):
    with pytest.raises(ValidationError):
        QuerySpec.model_validate(payload)


def test_no_arbitrary_sql_and_filter_parameters():
    with pytest.raises(ValueError, match="arbitrary SQL"):
        compile_query("SELECT * FROM raw.orders")
    sql, params = compile_query(QuerySpec(metric="item_sales", group_by="month", start="2017-08-01", end="2017-09-01", statuses=["delivered"], states=["SP"]))
    assert "analytics.order_facts" in str(sql)
    assert "< :end" in str(sql) and ">= :start" in str(sql)
    assert "delivered" not in str(sql)
    assert params["statuses"] == ["delivered"]


def test_metric_documents_support_deterministic_contract():
    docs = {d["document_id"]: d for d in source_documents()}
    for id, metric in METRICS.items():
        assert metric.formula in docs[id]["text"]
        assert metric.denominator in docs[id]["text"]
    assert "before joining" in docs["joins"]["text"]
    assert "provisional" in docs["schema:orders"]["source"]


def test_year_comparison_compiles_only_requested_years_in_chronological_order():
    spec = QuerySpec(metric="placed_orders", group_by="year", years=[2018, 2016, 2018],
                     start="2016-01-01", end="2019-01-01")
    assert spec.years == [2016, 2018]
    query, params = compile_query(spec)
    assert "EXTRACT(YEAR FROM order_purchase_timestamp)::int AS year" in str(query)
    assert "EXTRACT(YEAR FROM order_purchase_timestamp)::int IN" in str(query)
    assert "ORDER BY year" in str(query)
    assert params["years"] == [2016, 2018]
    assert "2016" not in str(query) and "2018" not in str(query)
    preview = preview_query(spec)
    assert "IN (2016, 2018)" in preview["display_query"]
    assert "'2016-01-01'" in preview["display_query"]
    assert "'2019-01-01'" in preview["display_query"]
    assert preview["parameters"]["years_1"] == 2016
    assert preview["parameters"]["years_2"] == 2018


@pytest.mark.parametrize("metric", ["placed_orders", "delivered_orders", "item_sales", "freight",
                                   "recorded_payments", "aov", "delivery_days", "late_delivery_rate"])
def test_order_level_metrics_support_calendar_year_grouping(metric):
    query, _ = compile_query(QuerySpec(metric=metric, group_by="year"))
    assert "FROM analytics.order_facts" in str(query)
    assert "GROUP BY 1 ORDER BY year" in str(query)


def test_repeated_import_reports_do_not_change_document_content():
    import_report = {"source_kind":"fixture", "files":{"orders":{"columns":["order_id","order_status"]}}}
    assert source_documents([import_report]) == source_documents([import_report,import_report])


def test_zero_baseline_and_nonadditive_check():
    assert percent_change(0, 12) is None
    assert percent_change(100, 120) == Decimal("20")
    assert check_result(sample())["sum_of_displayed_values"] == "250.00"
    assert check_result(sample(metric="aov"))["sum_of_displayed_values"] is None


def test_chart_uses_actual_rows():
    result = sample()
    spec = ChartSpec(result_id=result.result_id, chart_type="line", x="month", title="Item sales", units="BRL")
    figure = build_chart(spec, result)
    assert list(figure.data[0].y) == [230,20]
    assert figure.layout.yaxis.tickprefix == "R$ "


@pytest.mark.parametrize("changes,rows", [
    ({"result_id":"not-real"}, None), ({"x":"missing"}, None),
    ({"units":"orders"}, None), ({}, []),
    ({"chart_type":"pie"}, [{"month":"a","value":"-1"}]),
    ({"chart_type":"pie"}, [{"month":"a","value":"0"}]),
    ({"chart_type":"line"}, [{"month":"not-a-date","value":"1"}]),
    ({"chart_type":"bar"}, [{"month":"2017-08-01","value":None}]),
])
def test_chart_rejections(changes,rows):
    result=sample(rows=rows)
    base=dict(result_id=result.result_id,chart_type="bar",x="month",title="Test",units="BRL")
    with pytest.raises((ValueError, TypeError)):
        build_chart(ChartSpec(**dict(base,**changes)),result)


def test_truncated_pie_and_many_categories():
    result=sample()
    result.truncated=True
    with pytest.raises(ValueError,match="complete"):
        build_chart(ChartSpec(result_id=result.result_id,chart_type="pie",x="month",title="Test",units="BRL"),result)
    result=sample(rows=[{"month":str(i),"value":"1"} for i in range(31)])
    with pytest.raises(ValueError,match="Too many"):
        build_chart(ChartSpec(result_id=result.result_id,chart_type="bar",x="month",title="Test",units="BRL"),result)


def test_session_isolation_and_budget():
    store=MemoryStore(); result=sample(); store.save_result("a",result)
    with pytest.raises(ValueError):
        store.get_result("b",result.result_id)
    budget=ToolBudget(2,10,0)
    assert budget.run("test",{},lambda:1)==1
    assert budget.run("test",{},lambda:2)==2
    with pytest.raises(BudgetExceeded):
        budget.run("test",{},lambda:3)
    budget=ToolBudget(10,10,0)
    def fail():
        raise ValueError("bad column")
    assert "error" in budget.run("bad",{},fail)
    with pytest.raises(BudgetExceeded,match="Repeated"):
        budget.run("bad",{},fail)


def test_chart_preferences():
    assert not charts_allowed("plot this", "Never")
    assert not charts_allowed("Do not draw a chart", "Auto")
    assert not charts_allowed("monthly sales", "When requested")
    assert charts_allowed("turn this into a bar chart", "When requested")


def test_complete_payload_cap_includes_metadata_and_warns():
    result = sample(rows=[{"month":"2017-08-01", "value":"1", "large":"x"*4000}])
    bound_payload(result,2000)
    assert result.truncated and not result.rows
    assert len(result.model_dump_json().encode()) <= 2000
    assert any("truncated" in w for w in result.warnings)
    with pytest.raises(ValueError,match="metadata"):
        bound_payload(sample(),10)
