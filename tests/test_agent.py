"""Deterministic tool-calling model tests, NOT live LLM verification."""
import json
from typing import Any
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field
from olist_agent.agent.runtime import AgentService, BudgetExceeded
from olist_agent.config import Settings
from olist_agent.models import QuerySpec, QueryResult
from olist_agent.services.store import MemoryStore


class ScriptedModel(BaseChatModel):
    scripts: list[Any]
    position: int = 0
    bound_names: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "deterministic-test-mock"

    def bind_tools(self, tools, **kwargs):
        self.bound_names = [t["function"]["name"] if isinstance(t, dict) and "function" in t else t.get("name") if isinstance(t,dict) else t.name for t in tools]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        script = self.scripts[min(self.position, len(self.scripts)-1)]
        self.position += 1
        message = script(messages) if callable(script) else script
        message = message.model_copy(deep=True)
        for index, tool_call in enumerate(message.tool_calls):
            tool_call["id"] = f"call-{self.position}-{index}"
        return ChatResult(generations=[ChatGeneration(message=message)])


def call(name, args, id="call1"):
    # Older fixtures describe a scripted model's intended SQL using QuerySpec.
    # Compile only inside this test helper; production agents accept SQL text.
    if name == "execute_analytics_query" and "sql" not in args.get("spec", {}):
        from olist_agent.analytics.query import preview_query
        from olist_agent.models import SQLQuerySpec
        legacy = QuerySpec.model_validate(args["spec"])
        axis = {"year":"year","month":"month","state":"state","category":"category",
                "category_month":"month","payment_method":"payment_method"}.get(legacy.group_by)
        args = {"spec": SQLQuerySpec(sql=preview_query(legacy)["display_query"], metric=legacy.metric,
            x=axis, series="category" if legacy.group_by == "category_month" else None).model_dump(mode="json")}
    return AIMessage(content="", tool_calls=[{"name":name,"args":args,"id":id,"type":"tool_call"}])


def report(answer="Fixture answer", tables=None, charts=None, findings=None):
    return call("Report", {"answer":answer,"tables":tables or [],"charts":charts or [],"findings":findings or []})


class MockQuery:
    def __init__(self, store):
        self.store, self.calls = store, []

    def execute(self, spec, session):
        self.calls.append(spec)
        result = QueryResult(columns=["month","value"],types={"month":"date","value":"int"},
            rows=[{"month":"2017-08-01","value":2}],metric_id=spec.metric,spec=spec,query="MOCK SQL",parameters={},provenance="TEST FIXTURE")
        self.store.save_result(session,result)
        return result


class MockRetriever:
    def retrieve(self, query, limit):
        return [{"document_id":"placed_orders","title":"Placed orders","section":"metric","text":"Count distinct orders","source":"test","version":"1"}]


def setup_service(scripts, settings=None):
    store = MemoryStore()
    query = MockQuery(store)
    service = AgentService(ScriptedModel(scripts=scripts),query,MockRetriever(),store,settings or Settings(max_seconds=20))
    return service, query, store


def approve_all(service, session, output):
    """Explicit approvals belong to the test operator, never the agent runtime."""
    for _ in range(30):
        if "pending_query" not in output:
            return output
        output = service.resume(session, output["approval_id"], approve=True)
    pytest.fail("Agent kept requesting approval beyond the configured tool budget")


def test_real_create_agent_with_mock_model_executes_and_persists():
    def result_report(messages):
        result = json.loads(next(m.content for m in reversed(messages) if isinstance(m,ToolMessage) and m.name == "execute_analytics_query"))
        return report(tables=[result["result_id"]])
    service, query, store = setup_service([
        call("retrieve_context", {"query":"placed orders"}),
        call("get_metric_definition", {"metric_id":"placed_orders"}),
        call("execute_analytics_query", {"spec":{"metric":"placed_orders","group_by":"month"}}), result_report])
    output=approve_all(service,"session",service.run("session","Monthly orders"))
    assert len(query.calls)==1
    assert output["report"].tables
    assert "placed_orders" in output["report"].metric_ids
    assert output["citations"][0]["document_id"]=="placed_orders"
    assert len(store.reports)==1
    assert "TEST FIXTURE" in " ".join(output["report"].limitations)


def test_chart_only_followup_reuses_previous_result():
    service,query,store=setup_service([report()])
    previous=query.execute(QuerySpec(metric="placed_orders",group_by="month"),"session")
    spec={"result_id":previous.result_id,"chart_type":"bar","x":"month","title":"Orders","units":"orders"}
    service.model=ScriptedModel(scripts=[call("create_chart",{"spec":spec}),report(tables=[previous.result_id],charts=[spec])])
    output=service.run("session","Turn the previous result into a bar chart",previous_result_id=previous.result_id)
    assert len(query.calls)==1
    assert output["report"].charts[0].result_id==previous.result_id


def test_cited_result_is_displayed_when_model_omits_table_list():
    def findings_report(messages):
        result=json.loads(next(m.content for m in reversed(messages) if isinstance(m,ToolMessage) and m.name=="execute_analytics_query"))
        return report(findings=[{"text":"Two orders", "result_ids":[result["result_id"],result["result_id"]]}])
    service,query,store=setup_service([
        call("execute_analytics_query",{"spec":{"metric":"placed_orders","group_by":"month"}}),findings_report])
    output=approve_all(service,"session",service.run("session","Count orders"))
    keys=output["report"].tables
    assert len(keys)==1 and store.get_result("session",keys[0]).rows[0]["value"]==2
    assert store.reports[0][2].tables==keys


def test_changed_dates_execute_fresh_query():
    service,query,store=setup_service([report()])
    previous=query.execute(QuerySpec(metric="placed_orders",group_by="month",start="2017-08-01",end="2017-09-01"),"session")
    service.model=ScriptedModel(scripts=[call("execute_analytics_query",{"spec":{"metric":"placed_orders","group_by":"month","start":"2017-09-01","end":"2017-10-01"}}),report()])
    approve_all(service,"session",service.run("session","Now use September 2017",previous_result_id=previous.result_id))
    assert len(query.calls)==2
    assert "'2017-09-01'" in query.calls[-1].sql


def test_unknown_citations_rejected():
    service,_,_=setup_service([report(findings=[{"text":"Unsupported","document_ids":["invented"]}])])
    with pytest.raises(ValueError,match="not retrieved"):
        service.run("session","Count orders")


def test_chart_restriction_enforced_after_model_output():
    service,query,store=setup_service([report()])
    previous=query.execute(QuerySpec(metric="placed_orders",group_by="month"),"session")
    spec={"result_id":previous.result_id,"chart_type":"bar","x":"month","title":"Orders","units":"orders"}
    service.model=ScriptedModel(scripts=[report(charts=[spec])])
    with pytest.raises(ValueError,match="restriction"):
        service.run("session","Do not draw",previous_result_id=previous.result_id)


def test_loop_budget_stops_mock_model():
    service,_,_=setup_service([call("inspect_schema",{})],Settings(max_tool_calls=2,max_seconds=20))
    with pytest.raises(BudgetExceeded):
        service.run("session","Loop")


def test_repeated_failed_tool_is_blocked():
    service,_,_=setup_service([call("get_metric_definition",{"metric_id":"profit"})],Settings(max_tool_calls=10,max_retries=0,max_seconds=20))
    with pytest.raises(BudgetExceeded,match="Repeated"):
        service.run("session","profit")


def test_query_waits_for_approval_and_shows_exact_filters():
    service,query,store=setup_service([
        call("execute_analytics_query",{"spec":{"metric":"delivered_orders","group_by":"total",
            "start":"2017-08-01","end":"2017-09-01","states":["SP"]}}),
        report()])
    output=service.run("session","Count delivered orders in SP during August 2017")
    assert not query.calls and not store.results and not store.reports
    pending=output["pending_query"]
    assert "COUNT(DISTINCT order_id)" in pending["query"]
    assert "analytics.order_facts" in pending["query"]
    assert pending["spec"]["metric"]=="delivered_orders"
    assert "FILTER (WHERE order_status='delivered')" in pending["query"]
    assert "'2017-08-01'" in pending["query"]
    assert "'2017-09-01'" in pending["query"]
    assert "'SP'" in pending["query"]
    assert pending["parameters"] == {}
    assert output["review_index"]==output["review_count"]==1
    completed=service.resume("session",output["approval_id"],approve=True)
    assert len(query.calls)==1 and "report" in completed
    assert query.calls[0].model_dump(mode="json")==pending["spec"]


def test_reject_cancels_without_running_query_or_model_again():
    service,query,store=setup_service([
        call("execute_analytics_query",{"spec":{"metric":"placed_orders","group_by":"total"}}),
        report(answer="This answer must not be generated after rejecting")])
    output=service.run("session","Count orders")
    position=service.model.position
    rejected=service.resume("session",output["approval_id"],approve=False)
    assert not query.calls and not store.results
    assert service.model.position==position
    assert not rejected["report"].tables
    assert "reject" in rejected["report"].answer.lower()
    assert len(store.reports)==1


def test_approval_is_bound_to_session_and_consumed_once():
    service,query,store=setup_service([
        call("execute_analytics_query",{"spec":{"metric":"placed_orders","group_by":"total"}}),
        report()])
    output=service.run("session","Count orders")
    with pytest.raises(ValueError):
        service.resume("other-session",output["approval_id"],approve=True)
    assert not query.calls and not store.results
    service.resume("session",output["approval_id"],approve=True)
    assert len(query.calls)==1
    with pytest.raises(ValueError):
        service.resume("session",output["approval_id"],approve=True)
    assert len(query.calls)==1


@pytest.mark.parametrize("decision",[1,0,"yes",None])
def test_approval_requires_an_explicit_boolean(decision):
    service,query,_=setup_service([
        call("execute_analytics_query",{"spec":{"metric":"placed_orders","group_by":"total"}}),report()])
    output=service.run("session","Count orders")
    with pytest.raises(ValueError):
        service.resume("session",output["approval_id"],approve=decision)
    assert not query.calls
    service.resume("session",output["approval_id"],approve=False)


def test_each_followup_query_requires_a_new_approval():
    service,query,store=setup_service([
        call("execute_analytics_query",{"spec":{"metric":"placed_orders","group_by":"month"}}),
        call("execute_analytics_query",{"spec":{"metric":"placed_orders","group_by":"total"}}),report()])
    first=service.run("session","Compare monthly orders and reconcile the total")
    assert not query.calls
    second=service.resume("session",first["approval_id"],approve=True)
    assert len(query.calls)==1 and not store.reports
    assert second["pending_query"]["spec"]["x"] is None
    assert second["approval_id"]!=first["approval_id"]
    with pytest.raises(ValueError):
        service.resume("session",first["approval_id"],approve=True)
    assert len(query.calls)==1
    completed=service.resume("session",second["approval_id"],approve=True)
    assert len(query.calls)==2 and "report" in completed


def batched_queries():
    return AIMessage(content="",tool_calls=[
        call("execute_analytics_query",{"spec":{"metric":"placed_orders","group_by":"month"}},id="monthly").tool_calls[0],
        call("execute_analytics_query",{"spec":{"metric":"placed_orders","group_by":"total"}},id="total").tool_calls[0]])


def test_every_query_in_same_model_turn_is_reviewed_individually():
    service,query,store=setup_service([batched_queries(),report()])
    first=service.run("session","Monthly orders and their total")
    assert first["review_index"]==1 and first["review_count"]==2
    assert first["pending_query"]["spec"]["x"]=="month"
    second=service.resume("session",first["approval_id"],approve=True)
    assert not query.calls and not store.results and not store.reports
    assert second["review_index"]==2 and second["review_count"]==2
    assert second["pending_query"]["spec"]["x"] is None
    assert first["approval_id"]!=second["approval_id"]
    completed=service.resume("session",second["approval_id"],approve=True)
    assert len(query.calls)==2 and "report" in completed
    assert {spec.group_by for spec in query.calls}=={"month","total"}


def test_rejecting_one_query_cancels_entire_unexecuted_batch():
    service,query,store=setup_service([batched_queries(),report()])
    first=service.run("session","Monthly orders and their total")
    second=service.resume("session",first["approval_id"],approve=True)
    service.resume("session",second["approval_id"],approve=False)
    assert not query.calls and not store.results
    with pytest.raises(ValueError):
        service.resume("session",second["approval_id"],approve=True)


def test_waiting_for_user_does_not_consume_execution_time(monkeypatch):
    import olist_agent.agent.runtime as runtime
    actual_clock=runtime.monotonic
    offset=[0]
    monkeypatch.setattr(runtime,"monotonic",lambda: actual_clock()+offset[0])
    service,query,_=setup_service([
        call("execute_analytics_query",{"spec":{"metric":"placed_orders","group_by":"total"}}),report()],
        Settings(max_seconds=20))
    output=service.run("session","Count orders")
    offset[0]=3600
    completed=service.resume("session",output["approval_id"],approve=True)
    assert len(query.calls)==1 and "report" in completed


def test_mutating_displayed_preview_cannot_change_approved_execution():
    service,query,_=setup_service([
        call("execute_analytics_query",{"spec":{"metric":"placed_orders","group_by":"total"}}),report()])
    output=service.run("session","Count orders")
    output["pending_query"]["spec"]["metric"]="item_sales"
    output["pending_query"]["parameters"]["row_limit"]=99999
    output["pending_query"]["query"]="SELECT * FROM raw.customers"
    completed=service.resume("session",output["approval_id"],approve=True)
    assert len(query.calls)==1 and query.calls[0].metric=="placed_orders"
    assert "report" in completed


def test_changed_execution_limits_require_a_fresh_review():
    from dataclasses import replace
    service,query,store=setup_service([
        call("execute_analytics_query",{"spec":{"metric":"placed_orders","group_by":"total"}}),report()])
    output=service.run("session","Count orders")
    service.settings=replace(service.settings,max_rows=service.settings.max_rows+1)
    with pytest.raises(ValueError,match="changed"):
        service.resume("session",output["approval_id"],approve=True)
    assert not query.calls and not store.results and not store.reports


def test_invalid_query_spec_never_reaches_execution_or_review():
    service,query,store=setup_service([
        call("execute_analytics_query",{"spec":{"metric":"placed_orders",
            "sql":"DROP TABLE raw.orders"}})])
    with pytest.raises(BudgetExceeded):
        service.run("session","Run arbitrary SQL")
    assert not query.calls and not store.results and not store.reports


def provider_failure(messages):
    raise RuntimeError("503 provider payload contains fake-secret-never-display")


def test_provider_error_after_approved_query_preserves_saved_evidence():
    service,query,store=setup_service([
        call("execute_analytics_query",{"spec":{"metric":"placed_orders","group_by":"total"}}),
        provider_failure])
    pending=service.run("session","Count orders")
    assert not query.calls and not store.reports
    completed=service.resume("session",pending["approval_id"],approve=True)
    assert len(query.calls)==1 and len(store.results)==1 and len(store.reports)==1
    result=next(iter(store.results.values()))
    assert completed["report"].tables==[result.result_id]
    assert store.reports[0][2].tables==[result.result_id]
    assert "approved query ran" in completed["report"].answer
    assert "busy" in " ".join(completed["report"].limitations)
    assert "fake-secret" not in completed["report"].model_dump_json()
    assert completed["reviews"][0]["status"]=="executed"
    assert service.model.position==2
    with pytest.raises(ValueError):
        service.resume("session",pending["approval_id"],approve=True)
    assert len(query.calls)==1


def test_initial_provider_failure_does_not_invent_a_saved_answer():
    service,query,store=setup_service([provider_failure])
    with pytest.raises(RuntimeError,match="503"):
        service.run("session","Count orders")
    assert not query.calls and not store.results and not store.reports


def test_rejecting_followup_query_preserves_earlier_approved_evidence():
    service,query,store=setup_service([
        call("execute_analytics_query",{"spec":{"metric":"placed_orders","group_by":"month"}}),
        call("execute_analytics_query",{"spec":{"metric":"placed_orders","group_by":"total"}}),report()])
    first=service.run("session","Count monthly orders and reconcile")
    second=service.resume("session",first["approval_id"],approve=True)
    assert len(query.calls)==1 and not store.reports
    result=next(iter(store.results.values()))
    rejected=service.resume("session",second["approval_id"],approve=False)
    assert len(query.calls)==1 and len(store.reports)==1
    assert rejected["report"].tables==[result.result_id]
    assert store.reports[0][2].tables==[result.result_id]
    assert "Earlier approved results" in rejected["report"].answer
    assert [review["status"] for review in rejected["reviews"]]==["executed","rejected"]
    assert service.model.position==2


def test_invalid_model_report_after_query_uses_only_actual_saved_result():
    service,query,store=setup_service([
        call("execute_analytics_query",{"spec":{"metric":"placed_orders","group_by":"total"}}),
        report(answer="An unsupported invented answer",tables=["invented-result"])])
    pending=service.run("session","Count orders")
    completed=service.resume("session",pending["approval_id"],approve=True)
    assert len(query.calls)==1 and len(store.reports)==1
    actual=next(iter(store.results.values())).result_id
    assert completed["report"].tables==[actual]
    assert "invented-result" not in completed["report"].model_dump_json()
    assert "unsupported invented answer" not in completed["report"].answer
