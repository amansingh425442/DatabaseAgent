"""Regression coverage for the user's year comparison and approved chart flow.

The model and query executor are deterministic test doubles. The two counts are
independently checked against the original CSV during live verification.
"""
from olist_agent.agent.runtime import AgentService
from olist_agent.analytics.query import preview_query
from olist_agent.charts.render import build_chart
from olist_agent.config import Settings
from olist_agent.models import QueryResult, QuerySpec
from olist_agent.services.store import MemoryStore

from test_agent import MockRetriever, ScriptedModel, call, provider_failure, report


QUESTION = "comapare orders of 2016 and 2018 year with help of graph and charts"
YEAR_ROWS = [{"year": 2016, "value": 329, "missing_values": 0},
             {"year": 2018, "value": 54011, "missing_values": 0}]


def make_year_result(store, session, spec):
    preview = preview_query(spec)
    result = QueryResult(columns=["year", "value", "missing_values"],
        types={"year": "int", "value": "int", "missing_values": "int"},
        rows=YEAR_ROWS, metric_id=spec.metric, spec=spec,
        query=preview["query"], parameters=preview["parameters"],
        provenance="TEST FIXTURE", warnings=["Boundary years may be incomplete."])
    store.save_result(session, result)
    return result


class YearQuery:
    def __init__(self, store):
        self.store, self.calls = store, []

    def execute(self, spec, session):
        self.calls.append(spec)
        return make_year_result(self.store, session, spec)


def year_service(scripts):
    store = MemoryStore()
    query = YearQuery(store)
    model = ScriptedModel(scripts=scripts)
    service = AgentService(model, query, MockRetriever(), store, Settings(max_seconds=20))
    return service, query, store, model


def wrong_all_time_proposal():
    # The scripted LLM now authors the correct SQL; Python does not repair scope.
    return call("execute_analytics_query", {"spec": {
        "metric": "placed_orders", "group_by": "year", "years": [2016, 2018],
        "start": "2016-01-01", "end": "2019-01-01"}})


def assert_year_review(pending):
    preview = pending["pending_query"]
    spec = preview["spec"]
    assert spec["metric"] == "placed_orders"
    assert spec["x"] == "year"
    assert spec["sql"] == preview["display_query"]
    assert "EXTRACT(YEAR FROM order_purchase_timestamp)::int AS year" in preview["display_query"]
    assert "IN (2016, 2018)" in preview["display_query"]
    assert "ORDER BY year" in preview["display_query"]
    assert "customer_state IN" not in preview["display_query"]
    return preview


def test_model_authored_year_query_is_reviewed_and_rendered_after_approval():
    service, query, store, model = year_service([wrong_all_time_proposal(), provider_failure])
    pending = service.run("session", QUESTION)
    preview = assert_year_review(pending)
    assert not query.calls and not store.results and not store.reports
    assert model.position == 1

    completed = service.resume("session", pending["approval_id"], approve=True)
    assert len(query.calls) == 1
    assert query.calls[0].model_dump(mode="json") == preview["spec"]
    assert model.position == 1  # Approved rows need no second model/API response.
    saved = store.get_result("session", completed["report"].tables[0])
    assert saved.rows == YEAR_ROWS
    assert saved.query == preview["query"] and saved.parameters == preview["parameters"]
    assert "329" in completed["report"].answer and "54,011" in completed["report"].answer
    assert "could not be completed" not in completed["report"].answer
    assert len(completed["report"].charts) == 1
    chart = completed["report"].charts[0]
    assert chart.result_id == saved.result_id and chart.x == "year" and chart.units == "orders"
    figure = build_chart(chart, saved)
    assert list(figure.data[0].y) == [329, 54011]
    assert [str(value) for value in figure.data[0].x] == ["2016", "2018"]
    assert completed["reviews"][0]["status"] == "executed"
    assert len(store.reports) == 1


def test_new_year_comparison_cannot_reuse_a_previous_all_time_total():
    service, query, store, model = year_service([report(answer="There are 99,441 orders.")])
    previous = QueryResult(columns=["value"], types={"value": "int"},
        rows=[{"value": 99441}], metric_id="placed_orders",
        spec=QuerySpec(metric="placed_orders"), query="Stored all-time query", parameters={},
        provenance="TEST FIXTURE")
    store.save_result("session", previous)
    model.scripts = [wrong_all_time_proposal()]

    pending = service.run("session", QUESTION, previous_result_id=previous.result_id)
    assert_year_review(pending)
    assert not query.calls and len(store.results) == 1 and not store.reports
    completed = service.resume("session", pending["approval_id"], approve=True)
    assert len(query.calls) == 1
    assert previous.result_id not in completed["report"].tables
    assert "99,441" not in completed["report"].answer
    assert "54,011" in completed["report"].answer
    assert len(completed["report"].charts) == 1
    assert model.position == 1


def test_rejecting_the_corrected_year_query_never_fetches_data_or_draws_a_chart():
    service, query, store, model = year_service([wrong_all_time_proposal(), provider_failure])
    pending = service.run("session", QUESTION)
    assert_year_review(pending)
    rejected = service.resume("session", pending["approval_id"], approve=False)
    assert not query.calls and not store.results
    assert not rejected["report"].tables and not rejected["report"].charts
    assert "reject" in rejected["report"].answer.lower()
    assert model.position == 1


def test_plural_graphs_and_charts_followup_reuses_only_approved_saved_rows():
    service, query, store, model = year_service([wrong_all_time_proposal()])
    pending = service.run("session", QUESTION)
    completed = service.resume("session", pending["approval_id"], approve=True)
    approved_id = completed["report"].tables[0]
    followup_model = ScriptedModel(scripts=[provider_failure])
    service.model = followup_model

    followup = service.run("session", "Turn the previous result into graphs and charts",
        previous_result_id=approved_id, messages=completed["messages"])
    assert "pending_query" not in followup
    assert len(query.calls) == 1 and len(store.results) == 1
    assert followup_model.position == 0
    assert followup["report"].tables == [approved_id]
    assert followup["report"].charts[0].result_id == approved_id
    figure = build_chart(followup["report"].charts[0], store.get_result("session", approved_id))
    assert list(figure.data[0].y) == [329, 54011]
