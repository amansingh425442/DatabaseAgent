"""Real create_agent tests: the model writes SQL, Python never compiles a plan."""
from langchain_core.messages import SystemMessage, ToolMessage
from olist_agent.agent.runtime import AgentService
from olist_agent.config import Settings
from olist_agent.services.store import MemoryStore
from test_agent import MockQuery, MockRetriever, ScriptedModel, call, report
from test_generated_sql import YEAR_SQL


def test_model_receives_column_schema_and_its_sql_is_preserved(monkeypatch):
    import olist_agent.analytics.query as query_module
    def no_templates(*args, **kwargs):
        raise AssertionError("The agent must not construct SQL from QuerySpec templates")
    monkeypatch.setattr(query_module, "compile_query", no_templates)
    store = MemoryStore()
    query = MockQuery(store)
    sql = """WITH counted AS (
SELECT order_status AS status,COUNT(DISTINCT order_id) AS value
FROM analytics.order_facts WHERE order_purchase_timestamp >= '2017-01-01'
GROUP BY order_status)
SELECT status,value,DENSE_RANK() OVER (ORDER BY value DESC) AS ranking
FROM counted ORDER BY ranking"""
    def propose(messages):
        system = next(message.content for message in messages if isinstance(message, SystemMessage))
        assert "YOU write the PostgreSQL SQL" in system
        assert '"order_purchase_timestamp"' in system and '"item_sales_value"' in system
        assert '"category_items' in system or "analytics.category_items" in system
        assert "COUNT(DISTINCT order_id)" in system and "one-to-many" in system
        return call("execute_analytics_query", {"spec": {"sql": sql, "metric": "placed_orders"}})
    model = ScriptedModel(scripts=[propose, report()])
    service = AgentService(model, query, MockRetriever(), store, Settings(max_seconds=20))
    pending = service.run("session", "Rank order statuses in 2017")
    assert set(model.bound_names) == {
        "retrieve_context", "execute_analytics_query", "validate_sql",
        "check_result", "create_chart", "Report",
    }
    assert pending["pending_query"]["query"] == sql
    assert pending["pending_query"]["display_query"] == sql
    assert not query.calls and not store.results
    service.resume("session", pending["approval_id"], approve=True)
    assert len(query.calls) == 1 and query.calls[0].sql == sql


def test_invalid_sql_returns_feedback_to_model_and_repaired_sql_needs_approval():
    store = MemoryStore()
    query = MockQuery(store)
    def repair(messages):
        response = next(message for message in reversed(messages)
                        if isinstance(message, ToolMessage) and message.name == "validate_sql")
        assert "approved analytics views" in response.content.lower()
        return call("execute_analytics_query", {"spec": {"sql": YEAR_SQL, "metric": "placed_orders", "x": "year"}})
    model = ScriptedModel(scripts=[
        call("execute_analytics_query", {"spec": {"sql": "SELECT * FROM raw.orders"}}), repair, report()])
    service = AgentService(model, query, MockRetriever(), store, Settings(max_seconds=20))
    pending = service.run("session", "Compare orders in 2016 and 2018")
    assert model.position == 2 and not query.calls
    assert pending["pending_query"]["query"] == YEAR_SQL
    service.resume("session", pending["approval_id"], approve=False)
    assert not query.calls and not store.results and model.position == 2


def test_python_does_not_replace_a_safe_model_sql_with_a_calendar_template():
    # Semantic correctness still depends on the model and the user's SQL review.
    # This regression specifically proves there is no hidden template fallback.
    store = MemoryStore()
    query = MockQuery(store)
    sql = "SELECT COUNT(DISTINCT order_id) AS value FROM analytics.order_facts"
    model = ScriptedModel(scripts=[call("execute_analytics_query", {"spec": {"sql": sql}})])
    service = AgentService(model, query, MockRetriever(), store, Settings(max_seconds=20))
    pending = service.run("session", "Compare orders in 2016 and 2018")
    assert pending["pending_query"]["query"] == sql
    assert "GROUP BY" not in pending["pending_query"]["query"]
    service.resume("session", pending["approval_id"], approve=False)
    assert not query.calls
