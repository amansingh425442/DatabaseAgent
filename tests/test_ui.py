"""Streamlit interactions with deterministic model tools; never a live LLM."""
import json
from pathlib import Path

import pytest
import streamlit as st
from langchain_core.messages import ToolMessage
from streamlit.testing.v1 import AppTest

from test_agent import MockQuery, MockRetriever, ScriptedModel, call, report, provider_failure
from olist_agent.services.store import MemoryStore


APP_PATH=Path(__file__).parents[1]/"ui"/"app.py"


def button(app,label):
    return next(item for item in app.button if item.label==label)


@pytest.fixture(autouse=True)
def isolated_ui_cache():
    st.cache_resource.clear()
    yield
    st.cache_resource.clear()


@pytest.fixture
def connected_ui(monkeypatch):
    import olist_agent.agent.model as model_module
    import olist_agent.analytics.query as query_module
    import olist_agent.db.connection as connection_module
    import olist_agent.retrieval.documents as retrieval_module
    import olist_agent.services.store as store_module

    store=MemoryStore()
    store.db=None
    def history(session):
        return [{"id":str(index),"question":saved[1],
            "report":dict(saved[2].model_dump(mode="json"),**(saved[4] or {})),
            "messages":saved[3]} for index,saved in reversed(list(enumerate(store.reports)))
            if saved[0]==session]
    store.history=history
    query=MockQuery(store)
    def result_report(messages):
        result=json.loads(next(message.content for message in reversed(messages)
            if isinstance(message,ToolMessage) and message.name=="execute_analytics_query"))
        return report(answer="Two placed orders.",tables=[result["result_id"]])
    def create_model(factory):
        return ScriptedModel(scripts=[
            call("execute_analytics_query",{"spec":{"metric":"placed_orders","group_by":"total"}}),
            result_report])
    monkeypatch.setenv("ANALYTICS_DATABASE_URL","mock-analytics")
    monkeypatch.setenv("APP_DATABASE_URL","mock-history")
    monkeypatch.setenv("MODEL_FACTORY","tests.fake:create_model")
    monkeypatch.setattr(connection_module,"engine",lambda url: None)
    monkeypatch.setattr(store_module,"Store",lambda db: store)
    monkeypatch.setattr(query_module,"QueryService",lambda db,settings,store: query)
    monkeypatch.setattr(model_module,"load_model",create_model)
    monkeypatch.setattr(retrieval_module,"LocalEmbeddings",lambda model_id: object())
    monkeypatch.setattr(retrieval_module,"Retriever",lambda db,embeddings: MockRetriever())
    app=AppTest.from_file(str(APP_PATH)).run(timeout=20)
    assert not app.exception
    return app,query,store


def test_interface_renders_only_essential_controls_without_connection(monkeypatch):
    monkeypatch.setenv("ANALYTICS_DATABASE_URL","")
    monkeypatch.setenv("APP_DATABASE_URL","")
    monkeypatch.setenv("MODEL_FACTORY","")
    app=AppTest.from_file(str(APP_PATH)).run(timeout=20)
    assert not app.exception
    assert app.title[0].value=="Ask your database"
    assert app.chat_input[0].disabled
    assert any("No LLM provider" in info.value for info in app.info)
    assert app.sidebar.selectbox[0].label=="Sessions"
    assert app.sidebar.toggle[0].label=="Generate charts"
    assert button(app,"New session")
    assert any("History" in item.value for item in app.sidebar.markdown)
    assert not app.radio
    assert not any("explorer" in item.label.lower() for item in app.expander)
    assert not any("definitions" in item.label.lower() or "retrieved" in item.label.lower()
                   for item in app.expander)


def test_query_is_visible_and_requires_approval_before_execution(connected_ui):
    app,query,store=connected_ui
    assert not query.calls and not store.results
    app.chat_input[0].set_value("How many orders were placed?").run(timeout=20)
    assert not app.exception
    assert not query.calls and not store.results and not store.reports
    assert "COUNT(DISTINCT order_id)" in app.code[0].value
    assert "analytics.order_facts" in app.code[0].value
    assert any("has not run" in item.value for item in app.caption)
    assert app.chat_input[0].disabled and app.sidebar.toggle[0].disabled
    assert button(app,"Approve and run") and button(app,"Reject")
    button(app,"Approve and run").click().run(timeout=20)
    assert not app.exception
    assert len(query.calls)==1 and len(store.reports)==1
    assert len(app.dataframe)==1
    assert app.dataframe[0].value.iloc[0]["value"]==2
    assert not app.chat_input[0].disabled and not app.sidebar.toggle[0].disabled
    assert button(app,"How many orders were placed?")


def test_reject_does_not_execute_and_is_saved_in_history(connected_ui):
    app,query,store=connected_ui
    app.chat_input[0].set_value("Count all orders").run(timeout=20)
    button(app,"Reject").click().run(timeout=20)
    assert not app.exception
    assert not query.calls and not store.results
    assert len(store.reports)==1 and not app.dataframe
    assert any("rejected" in item.value.lower() for item in app.markdown)
    assert button(app,"Count all orders")
    assert not app.chat_input[0].disabled


def test_switching_sessions_preserves_pending_approval_and_history(connected_ui):
    app,query,store=connected_ui
    first_session=app.session_state["active_session"]
    app.chat_input[0].set_value("First session question").run(timeout=20)
    first_approval=app.session_state["sessions"][first_session]["pending"]["approval_id"]
    button(app,"New session").click().run(timeout=20)
    assert not app.exception
    second_session=app.session_state["active_session"]
    assert second_session!=first_session
    assert not app.chat_input[0].disabled and not app.code
    options=app.sidebar.selectbox[0].options
    assert len(options)==2 and "First session question" in options[0] and "Session" in options[1]
    app.chat_input[0].set_value("Second session question").run(timeout=20)
    button(app,"Approve and run").click().run(timeout=20)
    assert len(query.calls)==1 and len(store.reports)==1
    assert button(app,"Second session question")
    app.sidebar.selectbox[0].select_index(0).run(timeout=20)
    assert not app.exception
    assert app.session_state["active_session"]==first_session
    assert app.session_state["sessions"][first_session]["pending"]["approval_id"]==first_approval
    assert app.chat_input[0].disabled and button(app,"Approve and run")
    assert not any(item.label=="Second session question" for item in app.button)
    button(app,"Approve and run").click().run(timeout=20)
    assert not app.exception
    assert len(query.calls)==2 and len(store.reports)==2
    assert button(app,"First session question")
    app.sidebar.selectbox[0].select_index(1).run(timeout=20)
    assert not app.exception
    assert app.session_state["active_session"]==second_session
    assert button(app,"Second session question")
    assert not any(item.label=="First session question" for item in app.button)
    button(app,"Second session question").click().run(timeout=20)
    assert button(app,"Back to chat") and app.chat_input[0].disabled
    button(app,"Back to chat").click().run(timeout=20)
    assert not app.exception and not app.chat_input[0].disabled


def test_chart_choice_is_preserved_separately_for_each_session(connected_ui):
    app,query,store=connected_ui
    first_session=app.session_state["active_session"]
    app.sidebar.toggle[0].set_value(True).run(timeout=20)
    assert app.session_state["sessions"][first_session]["charts"]
    button(app,"New session").click().run(timeout=20)
    second_session=app.session_state["active_session"]
    assert second_session!=first_session
    assert len(set(app.sidebar.selectbox[0].options))==2
    assert not app.sidebar.toggle[0].value
    app.sidebar.selectbox[0].select_index(0).run(timeout=20)
    assert app.session_state["active_session"]==first_session
    assert not app.exception and app.sidebar.toggle[0].value
    app.sidebar.selectbox[0].select_index(1).run(timeout=20)
    assert not app.exception and not app.sidebar.toggle[0].value
    assert not query.calls and not store.reports


def test_identical_questions_in_different_sessions_have_unique_selection(connected_ui):
    app,query,store=connected_ui
    first_session=app.session_state["active_session"]
    app.chat_input[0].set_value("Count all orders").run(timeout=20)
    first_approval=app.session_state["sessions"][first_session]["pending"]["approval_id"]
    button(app,"New session").click().run(timeout=20)
    second_session=app.session_state["active_session"]
    app.chat_input[0].set_value("Count all orders").run(timeout=20)
    second_approval=app.session_state["sessions"][second_session]["pending"]["approval_id"]
    assert first_approval!=second_approval
    assert len(set(app.sidebar.selectbox[0].options))==2
    app.sidebar.selectbox[0].select_index(0).run(timeout=20)
    assert not app.exception and app.session_state["active_session"]==first_session
    assert app.session_state["sessions"][first_session]["pending"]["approval_id"]==first_approval
    button(app,"Reject").click().run(timeout=20)
    app.sidebar.selectbox[0].select_index(1).run(timeout=20)
    assert not app.exception and app.session_state["active_session"]==second_session
    assert app.session_state["sessions"][second_session]["pending"]["approval_id"]==second_approval
    assert button(app,"Approve and run") and app.chat_input[0].disabled
    assert not query.calls and not store.results and len(store.reports)==1


def test_post_query_provider_failure_keeps_table_and_history(connected_ui,monkeypatch):
    import olist_agent.agent.model as model_module
    app,query,store=connected_ui
    model=ScriptedModel(scripts=[
        call("execute_analytics_query",{"spec":{"metric":"placed_orders","group_by":"total"}}),
        provider_failure])
    monkeypatch.setattr(model_module,"load_model",lambda factory: model)
    app.chat_input[0].set_value("Count orders despite a later API error").run(timeout=20)
    assert not query.calls and not store.reports
    assert button(app,"Approve and run")
    button(app,"Approve and run").click().run(timeout=20)
    assert not app.exception
    assert len(query.calls)==1 and len(store.reports)==1 and len(app.dataframe)==1
    assert app.dataframe[0].value.iloc[0]["value"]==2
    assert any("approved query ran" in item.value for item in app.markdown)
    assert not any("fake-secret" in item.value for item in app.markdown)
    assert button(app,"Count orders despite a later API error")
    assert not app.chat_input[0].disabled
    assert not any(item.label=="Approve and run" for item in app.button)


def test_rejecting_followup_preserves_first_approved_table_in_ui(connected_ui,monkeypatch):
    import olist_agent.agent.model as model_module
    app,query,store=connected_ui
    model=ScriptedModel(scripts=[
        call("execute_analytics_query",{"spec":{"metric":"placed_orders","group_by":"month"}}),
        call("execute_analytics_query",{"spec":{"metric":"placed_orders","group_by":"total"}}),report()])
    monkeypatch.setattr(model_module,"load_model",lambda factory: model)
    app.chat_input[0].set_value("Monthly orders and reconciliation").run(timeout=20)
    button(app,"Approve and run").click().run(timeout=20)
    assert len(query.calls)==1 and not store.reports
    assert button(app,"Reject")
    button(app,"Reject").click().run(timeout=20)
    assert not app.exception
    assert len(query.calls)==1 and len(store.reports)==1 and len(app.dataframe)==1
    assert any("Earlier approved results" in item.value for item in app.markdown)
    assert button(app,"Monthly orders and reconciliation")
    assert not app.chat_input[0].disabled


def test_chart_is_rendered_from_approved_query_when_toggle_is_enabled(connected_ui,monkeypatch):
    import olist_agent.agent.model as model_module
    app,query,store=connected_ui
    def chart_request(messages):
        result=json.loads(next(message.content for message in reversed(messages)
            if isinstance(message,ToolMessage) and message.name=="execute_analytics_query"))
        return call("create_chart",{"spec":{"result_id":result["result_id"],"chart_type":"bar",
            "x":"month","y":"value","title":"Monthly orders","units":"orders"}})
    def chart_report(messages):
        validated=json.loads(next(message.content for message in reversed(messages)
            if isinstance(message,ToolMessage) and message.name=="create_chart"))["chart"]
        return report(answer="Monthly orders chart.",tables=[validated["result_id"]],charts=[validated])
    model=ScriptedModel(scripts=[
        call("execute_analytics_query",{"spec":{"metric":"placed_orders","group_by":"month"}}),
        chart_request,chart_report])
    monkeypatch.setattr(model_module,"load_model",lambda factory: model)
    app.sidebar.toggle[0].set_value(True).run(timeout=20)
    app.chat_input[0].set_value("Draw monthly placed orders").run(timeout=20)
    assert not app.exception and not query.calls and not store.reports
    assert not app.get("plotly_chart") and not app.dataframe
    button(app,"Approve and run").click().run(timeout=20)
    assert not app.exception
    assert len(query.calls)==1 and len(store.reports)==1
    assert len(app.get("plotly_chart"))==1 and len(app.dataframe)==1
    assert button(app,"Draw monthly placed orders")
