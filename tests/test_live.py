import os
import re
from uuid import uuid4
import pytest


@pytest.mark.live
def test_optional_live_model():
    if os.getenv("OLIST_RUN_LIVE")!="1":
        pytest.skip("Live LLM not requested; deterministic mocks do not verify live integration")
    from olist_agent.config import Settings
    from olist_agent.db.connection import engine
    from olist_agent.services.store import Store
    from olist_agent.analytics.query import QueryService
    from olist_agent.retrieval.documents import LocalEmbeddings, Retriever
    from olist_agent.agent.model import load_model
    from olist_agent.agent.runtime import AgentService
    settings=Settings.load()
    store=Store(engine(settings.app_url))
    queries=QueryService(engine(settings.analytics_url),settings,store)
    expected=queries.coverage()["orders"]
    agent=AgentService(load_model(settings.model_factory),queries,
        Retriever(engine(settings.app_url),LocalEmbeddings(settings.embedding_model)),store,settings)
    session_id=str(uuid4())
    result=agent.run(session_id,"How many placed orders are recorded? Do not draw.")
    assert "pending_query" in result
    assert not store.history(session_id)
    # The opt-in live test operator reviews and authorizes each displayed query.
    for _ in range(settings.max_tool_calls):
        if "pending_query" not in result:
            break
        assert result["pending_query"]["query"]
        assert result["pending_query"]["spec"]["metric"]=="placed_orders"
        result=agent.resume(session_id,result["approval_id"],approve=True)
    assert "pending_query" not in result
    assert result["report"].tables
    assert not result["report"].charts
    evidence=[store.get_result(session_id, key) for key in result["report"].tables]
    # Explicitly selecting all states/statuses is equivalent to an empty filter.
    # Compare the executed count with whole-dataset coverage rather than syntax.
    totals=[r for r in evidence if r.metric_id=="placed_orders" and r.spec.group_by=="total"]
    assert totals and int(totals[0].rows[0]["value"])==expected
    assert str(expected) in re.sub(r"[,\s]", "", result["report"].answer)
    assert any(event["tool"]=="execute_analytics_query" and event["status"]=="complete" for event in result["activity"])
