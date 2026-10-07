"""Optional REAL PostgreSQL tests. Fresh disposable database, never user tables."""
import json
import os
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, text, event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from olist_agent.db.bootstrap import initialize
from olist_agent.db.connection import readonly
from olist_agent.ingestion.importer import import_csvs
from olist_agent.analytics.query import QueryService, preview_query
from olist_agent.config import Settings
from olist_agent.models import QuerySpec, SQLQuerySpec
from olist_agent.services.store import Store
from olist_agent.retrieval.documents import index_documents, Retriever

pytestmark=pytest.mark.integration
RAW=Path(__file__).parent/"fixtures"/"raw"


@pytest.fixture(scope="module")
def database():
    url=os.getenv("OLIST_TEST_ADMIN_URL")
    if not url:
        pytest.skip("OLIST_TEST_ADMIN_URL not set; actual PostgreSQL test not run")
    admin_url=make_url(url)
    catalog=create_engine(admin_url.set(database="postgres"),isolation_level="AUTOCOMMIT")
    name="olist_test_"+uuid4().hex
    with catalog.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    db=create_engine(admin_url.set(database=name))
    analytic=app=None
    try:
        initialize(db)
        report=import_csvs(db,RAW,source_kind="fixture")
        analytic=create_engine(admin_url.set(database=name,username="olist_analytics",password=os.environ["ANALYTICS_PASSWORD"]))
        app=create_engine(admin_url.set(database=name,username="olist_app",password=os.environ["APP_PASSWORD"]))
        store=Store(app)
        service=QueryService(analytic,Settings(),store)
        yield db, analytic, app, service, store, report
    finally:
        for resource in (db,analytic,app):
            if resource:
                resource.dispose()
        with catalog.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        catalog.dispose()


def scalar(service,metric,**kwargs):
    value=service.execute(QuerySpec(metric=metric,**kwargs),"test").rows[0]["value"]
    return Decimal(str(value)) if value is not None else None


def test_preview_is_offline_and_execution_reads_only_reviewed_query(database):
    _, analytics, _, service, _, _ = database
    statements = []

    def trace(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(analytics, "before_cursor_execute", trace)
    try:
        spec = QuerySpec(metric="placed_orders", start="2017-08-01", end="2017-09-01",
                         statuses=["delivered"])
        preview = preview_query(spec, service.settings.max_rows)
        assert not statements
        assert "'2017-08-01'" in preview["display_query"]
        assert "'delivered'" in preview["display_query"]
        result = service.execute(spec, "reviewed-query")
        business_reads = [statement for statement in statements if "FROM analytics." in statement]
        assert len(business_reads) == 1  # No hidden coverage/total query after approval.
        assert result.query == preview["query"] and result.parameters == preview["parameters"]
    finally:
        event.remove(analytics, "before_cursor_execute", trace)


def test_aggregates_no_join_multiplication(database):
    _,_,_,service,_,_=database
    assert scalar(service,"placed_orders")==4
    assert scalar(service,"delivered_orders")==3
    assert scalar(service,"item_sales")==250
    assert scalar(service,"freight")==25
    assert scalar(service,"recorded_payments")==275
    assert scalar(service,"aov")==Decimal("62.5")
    assert scalar(service,"delivery_days")==Decimal("7.5")
    assert scalar(service,"late_delivery_rate")==50


def test_generated_sql_schema_exact_execution_and_literal_characters(database):
    _, analytics, _, service, store, _ = database
    schema = service.schema_context()
    assert schema["views"]["analytics.order_facts"]["columns"]["order_id"] == "character varying"
    statements = []
    def trace(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(analytics, "before_cursor_execute", trace)
    try:
        sql = """WITH totals AS (
SELECT order_status AS status,COUNT(DISTINCT order_id) AS value
FROM analytics.order_facts WHERE order_status LIKE '%ed%' GROUP BY order_status)
SELECT status,value,DENSE_RANK() OVER (ORDER BY value DESC) AS ranking,
':not_a_bind' AS literal FROM totals ORDER BY ranking;"""
        spec = SQLQuerySpec(sql=sql, metric="placed_orders", x="status")
        preview = preview_query(spec)
        assert not statements  # Offline preview has no database access.
        result = service.execute(spec, "generated-sql")
        business = [statement for statement in statements if "WITH totals AS" in statement]
        assert business == [sql]  # No wrapper/rewrite/template; the reviewed text runs.
        assert result.query == sql and result.parameters == {}
        assert result.rows[0]["status"] == "delivered" and result.rows[0]["value"] == 3
        assert result.rows[0]["literal"] == ":not_a_bind"
        assert store.get_result("generated-sql", result.result_id).spec.sql == sql
        assert preview["query"] == result.query
        for bad in ("SELECT * FROM raw.orders", "SELECT pg_sleep(1) FROM analytics.order_facts", "DELETE FROM analytics.order_facts"):
            with pytest.raises(ValueError):
                service.execute(SQLQuerySpec(sql=bad), "generated-sql")
        limited = QueryService(analytics, Settings(max_rows=2), store)
        result = limited.execute(SQLQuerySpec(sql="SELECT order_id FROM analytics.order_facts ORDER BY order_id"), "generated-limit")
        assert result.truncated and len(result.rows) == 2
    finally:
        event.remove(analytics, "before_cursor_execute", trace)


def test_status_date_empty_and_reconciliation(database):
    _,_,_,service,_,_=database
    assert scalar(service,"item_sales",statuses=["delivered"])==230
    assert scalar(service,"placed_orders",start="2017-09-01",end="2017-10-01")==2
    assert scalar(service,"item_sales",start="2017-08-01",end="2017-09-01")==230
    assert scalar(service,"item_sales",start="2020-01-01",end="2021-01-01")==0
    assert scalar(service,"aov",start="2020-01-01",end="2021-01-01") is None
    result=service.execute(QuerySpec(metric="category_sales",group_by="category"),"test")
    assert sum(Decimal(row["value"]) for row in result.rows)==scalar(service,"item_sales")


def test_noncontiguous_years_use_purchase_dates_and_exclude_intervening_year(database):
    db, _, _, service, _, _ = database
    rows = [
        {"order_id": "year-test-before", "purchased": "2015-12-31 23:59:59", "status": "delivered"},
        {"order_id": "year-test-2016-start", "purchased": "2016-01-01 00:00:00", "status": "delivered"},
        {"order_id": "year-test-2016-end", "purchased": "2016-12-31 23:59:59", "status": "delivered"},
        {"order_id": "year-test-between", "purchased": "2017-01-01 00:00:00", "status": "delivered"},
        {"order_id": "year-test-2018-start", "purchased": "2018-01-01 00:00:00", "status": "delivered"},
        {"order_id": "year-test-2018-end", "purchased": "2018-12-31 23:59:59", "status": "canceled"},
        {"order_id": "year-test-after", "purchased": "2019-01-01 00:00:00", "status": "delivered"},
    ]
    with db.begin() as conn:
        conn.execute(text("INSERT INTO raw.orders (order_id,customer_id,order_status,order_purchase_timestamp) "
                          "VALUES (:order_id,'c1',:status,CAST(:purchased AS timestamp))"), rows)
    try:
        spec = QuerySpec(metric="placed_orders", group_by="year", years=[2018, 2016],
                         start="2016-01-01", end="2019-01-01")
        preview = preview_query(spec, service.settings.max_rows)
        result = service.execute(spec, "year-comparison")
        assert [(row["year"], row["value"]) for row in result.rows] == [(2016, 2), (2018, 2)]
        assert result.types["year"] == "int"
        assert result.query == preview["query"]
        assert result.parameters == preview["parameters"]
        delivered = service.execute(spec.model_copy(update={"metric": "delivered_orders"}), "year-comparison")
        assert [(row["year"], row["value"]) for row in delivered.rows] == [(2016, 2), (2018, 1)]
        assert scalar(service, "placed_orders", years=[2016, 2018]) == 4
    finally:
        with db.begin() as conn:
            conn.execute(text("DELETE FROM raw.orders WHERE order_id LIKE 'year-test-%'"))


def test_evaluation_answers(database):
    service=database[3]
    for case in json.loads((Path(__file__).parent/"evaluations.json").read_text()):
        result=service.execute(QuerySpec.model_validate(case["spec"]),"test")
        columns=[c for c in result.columns if c not in ("value","amount","missing_values")]
        actual={"/".join(str(row[c]) for c in columns):Decimal(str(row["value"])) for row in result.rows}
        assert actual=={key:Decimal(value) for key,value in case["expected"].items()}


def test_repeatable_import_and_duplicate_source_rows(database):
    db,_,_,_,_,report=database
    assert report.files["order_reviews"].imported==3
    assert report.files["order_reviews"].repeated_source_rows==1
    assert report.files["geolocation"].imported==2
    repeat=import_csvs(db,RAW,source_kind="fixture")
    assert all(file.imported==0 for file in repeat.files.values())
    assert repeat.files["orders"].duplicates==4
    with pytest.raises(ValueError,match="mix"):
        import_csvs(db,RAW,source_kind="olist")


def test_db_permissions_and_session_rls(database):
    _,analytic,app,service,store,_=database
    for sql in ("SELECT * FROM raw.orders", "CREATE TABLE analytics.evil(id int)", "DELETE FROM analytics.order_facts"):
        with pytest.raises(DBAPIError):
            with analytic.begin() as conn:
                conn.execute(text(sql))
    with readonly(analytic) as conn:
        with pytest.raises(DBAPIError):
            conn.execute(text("CREATE TEMP TABLE evil(id int)"))
    result=service.execute(QuerySpec(metric="placed_orders"),"session-a")
    with pytest.raises(ValueError):
        store.get_result("session-b",result.result_id)
    with app.begin() as conn:
        conn.execute(text("SELECT set_config('app.session_id','session-b',true)"))
        assert conn.execute(text("SELECT COUNT(*) FROM app.results")).scalar()==0


class MockEmbeddings:
    """Tests database vector path; does NOT verify Sentence Transformers quality."""
    model_id="test-embedding-384"
    def encode(self,texts):
        return [[1.0]+[0.0]*383 for _ in texts]


def test_real_pgvector_roundtrip_repeatable_index(database):
    db,_,app,service,_,_=database
    encoder=MockEmbeddings()
    first=index_documents(db,encoder,service.coverage())
    second=index_documents(db,encoder,service.coverage())
    assert first["embedded"]>0 and second["embedded"]==0
    passages=Retriever(app,encoder).retrieve("orders",3)
    assert len(passages)==3 and all(p["model"]==encoder.model_id for p in passages)
