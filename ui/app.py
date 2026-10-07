"""Minimal chat UI with session history and explicit approval for every analytics query."""
import re
from uuid import uuid4

import pandas as pd
import streamlit as st

from olist_agent.analytics.metrics import METRICS
from olist_agent.analytics.presentation import result_title, value_column
from olist_agent.analytics.query import QueryService
from olist_agent.agent.intent import parse_question
from olist_agent.charts.render import build_chart
from olist_agent.config import Settings
from olist_agent.db.connection import engine
from olist_agent.models import Report
from olist_agent.services.store import Store

st.set_page_config(page_title="Olist Chat", page_icon="📊", layout="centered")
st.markdown("""<style>
.stApp {background:#f8fafc;} h1,h2,h3 {color:#182d46;}
[data-testid="stSidebar"] {background:#eef2f7;}
[data-testid="stToolbar"] {display:none;}
[data-testid="stChatMessage"] {background:white;border:1px solid #e3eaf2;border-radius:12px;}
</style>""", unsafe_allow_html=True)


def new_session():
    session_id = str(uuid4())
    number = len(st.session_state.sessions) + 1
    st.session_state.sessions[session_id] = {
        "number": number, "label": f"Session {number}", "entries": [], "messages": [], "previous_result": None,
        "pending": None, "agent": None, "view_history": None, "loaded": False, "charts": False,
    }
    st.session_state.active_session = session_id


if "sessions" not in st.session_state:
    st.session_state.sessions = {}
    new_session()

settings = Settings.load()


@st.cache_resource(show_spinner=False)
def services(service_settings):
    store = Store(engine(service_settings.app_url))
    return store, QueryService(engine(service_settings.analytics_url), service_settings, store)


@st.cache_resource(show_spinner=False)
def cached_embeddings(model_id):
    from olist_agent.retrieval.documents import LocalEmbeddings
    return LocalEmbeddings(model_id)


class LazyEmbeddings:
    """Load local weights only when a question actually needs document retrieval."""
    def __init__(self, model_id):
        self.model_id = model_id

    def encode(self, texts):
        return cached_embeddings(self.model_id).encode(texts)


store = queries = None
connection_error = None
try:
    store, queries = services(settings)
except Exception:
    connection_error = "Connect the local database to start chatting."

with st.sidebar:
    st.subheader("Olist Chat")
    if st.button("New session", width="stretch"):
        new_session()
        st.rerun()
    labels = {key: value["label"] for key, value in st.session_state.sessions.items()}
    session_id = st.selectbox("Sessions", list(labels),
        format_func=lambda key: labels[key], key="active_session")
    session = st.session_state.sessions[session_id]
    chart_key = f"charts_{session_id}"
    synchronize_charts = session.pop("sync_charts", False)
    if synchronize_charts or chart_key not in st.session_state:
        st.session_state[chart_key] = session["charts"]
    charts = st.toggle("Generate charts", key=chart_key,
                       disabled=session["pending"] is not None)
    session["charts"] = charts
    if store and not session["loaded"]:
        try:
            saved = list(reversed(store.history(session_id)))
            session["entries"] = [{"id": entry["id"], "question": entry["question"],
                "report": Report.model_validate(entry["report"]),
                "reviews": entry["report"].get("_reviews", [])} for entry in saved]
            if saved:
                session["messages"] = saved[-1]["messages"]
                tables = saved[-1]["report"].get("tables", [])
                session["previous_result"] = tables[-1] if tables else None
            session["loaded"] = True
        except Exception:
            connection_error = "The local database is unavailable. Start PostgreSQL and refresh."
    st.divider()
    st.markdown("**History**")
    if not session["entries"]:
        st.caption("Your questions will appear here.")
    for entry in reversed(session["entries"]):
        if st.button(entry["question"][:65], key=f"history_{entry['id']}", width="stretch"):
            session["view_history"] = entry["id"]
            st.rerun()

st.title("Ask your database")
st.caption("Ask a question, review the SQL, then approve it to get an answer.")
if connection_error:
    st.warning(connection_error)
if not settings.model_factory:
    st.info("No LLM provider is connected yet.")


def show_sql(preview):
    sql = preview.get("display_query", preview["query"])
    if preview.get("spec", {}).get("sql"):
        st.code(sql, language="sql", wrap_lines=True)
        return
    sql = re.sub(r"\s+(FROM|WHERE|GROUP BY|ORDER BY|LIMIT)\s+",
        lambda match: match.group(0) if match.group(1) == "FROM" and sql[:match.start()].endswith("EXTRACT(YEAR")
        else "\n" + match.group(1) + " ", sql)
    st.code(sql, language="sql", wrap_lines=True)


def display(entry):
    with st.chat_message("user"):
        st.write(entry["question"])
    with st.chat_message("assistant"):
        for index, review in enumerate(entry.get("reviews", []), 1):
            with st.expander(f"Query {index} · {review['status'].capitalize()}"):
                show_sql(review)
        report = entry["report"]
        st.write(report.answer)
        for result_id in report.tables:
            result = store.get_result(session_id, result_id)
            frame = pd.DataFrame(result.rows)
            if "missing_values" in frame and not pd.to_numeric(frame["missing_values"], errors="coerce").fillna(0).any():
                frame = frame.drop(columns=["missing_values"])
            st.dataframe(frame, width="stretch", hide_index=True,
                         column_config={"year": "Year", value_column(result) or "value": result_title(result), "missing_values": "Missing values"})
            if result.truncated:
                st.caption("Results are limited; the table does not show the full population.")
        for chart in report.charts:
            result = store.get_result(session_id, chart.result_id)
            st.plotly_chart(build_chart(chart, result), width="stretch")
        for note in report.limitations:
            if note.startswith(("Calendar-year", "No result row", "There are no matching", "A graph", "A single total", "Used a")):
                st.caption(note)


def accept_response(response):
    if "pending_query" in response:
        session["pending"] = response
    else:
        session["pending"] = None
        session["agent"] = None
        session["entries"].append(dict(response, question=session["question"], id=str(uuid4())))
        session["messages"] = response["messages"]
        if response["report"].tables:
            session["previous_result"] = response["report"].tables[-1]


def show_error(exc):
    from olist_agent.agent.model import model_error_message
    session["pending"] = None
    session["agent"] = None
    session["error"] = str(exc) if isinstance(exc, ValueError) else model_error_message(exc)


view_history = session["view_history"]
if view_history and st.button("Back to chat"):
    session["view_history"] = None
    st.rerun()
entries = [entry for entry in session["entries"] if not view_history or entry["id"] == view_history]
for entry in entries:
    try:
        display(entry)
    except Exception:
        st.error("This answer could not be loaded. Check the local database connection.")

if not entries and not session["pending"]:
    st.write("Try: How many orders were placed each month in 2017?")
if session.get("error"):
    st.error(session["error"])

pending = session["pending"]
if pending and not view_history:
    with st.chat_message("user"):
        st.write(pending["question"])
    with st.chat_message("assistant"):
        st.markdown("**Review the proposed query**")
        preview = pending["pending_query"]
        st.caption("SQL generated by Gemini · Review before running")
        show_sql(preview)
        st.caption("Waiting for your approval. This query has not run.")
        if pending["review_count"] > 1:
            st.caption(f"Query {pending['review_index']} of {pending['review_count']}. All queries in this batch need approval before they run.")
        approve_column, reject_column = st.columns(2)
        with approve_column:
            approved = st.button("Approve and run", type="primary", key=f"approve_{pending['approval_id']}", width="stretch")
        with reject_column:
            rejected = st.button("Reject", key=f"reject_{pending['approval_id']}", width="stretch")
        if approved or rejected:
            try:
                with st.spinner("Running the approved query…" if approved else "Rejecting query…"):
                    response = session["agent"].resume(session_id, pending["approval_id"], approve=approved)
                    accept_response(response)
            except Exception as exc:
                show_error(exc)
            st.rerun()

question = st.chat_input("Ask a question about the database…", disabled=bool(connection_error)
    or not settings.model_factory or pending is not None or bool(view_history))
if question:
    session["question"] = question
    session.pop("error", None)
    intent = parse_question(question)
    if intent.chart_requested or intent.chart_forbidden:
        charts = intent.chart_requested and not intent.chart_forbidden
        session["charts"] = charts
        session["sync_charts"] = True
    if not session["entries"]:
        session["label"] = f"{session['number']}. {question[:45]}"
    try:
        from olist_agent.agent.model import load_model
        from olist_agent.agent.runtime import AgentService
        from olist_agent.retrieval.documents import Retriever
        with st.spinner("Preparing your query…"):
            agent = AgentService(load_model(settings.model_factory), queries,
                Retriever(store.db, LazyEmbeddings(settings.embedding_model)), store, settings)
            session["agent"] = agent
            response = agent.run(session_id, question, "Auto" if charts else "Never",
                                 session["messages"], session["previous_result"])
            accept_response(response)
    except Exception as exc:
        show_error(exc)
    st.rerun()
