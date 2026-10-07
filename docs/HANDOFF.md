Build the project described below. Treat this as the authoritative project handoff. Inspect the existing workspace first, then implement working code incrementally. Do not stop at a plan.

PROJECT: Olist Analytics Agent

GOAL

Build an application where users ask natural-language questions about the Olist e-commerce dataset. An LLM agent retrieves relevant schema and metric documentation, decomposes complex requests into analytical tasks, calls database tools, inspects results, and returns accurate answers with tables and optional charts.

Example requests:
- How many orders were placed each month in 2017?
- Which categories generated the highest item sales value?
- Compare category sales between August and September 2017.
- Show payment-method shares as a pie chart.
- Which states had the most orders?
- Plot monthly sales as a line graph.
- Show the query used to produce this result.
- Turn the previous result into a bar chart.

The agent should decide what information to retrieve, which queries to run, whether further checks are needed, and whether a visualization is useful.

FIXED TECHNOLOGY STACK

Use:
- Python.
- PostgreSQL for business data, documents, and investigation history.
- pgvector inside PostgreSQL for embedding retrieval.
- SQLAlchemy + psycopg for database access.
- LangChain’s current supported create_agent interface for the tool-calling agent.
- Sentence Transformers for local document embeddings.
- Pydantic for tool arguments, reports, and chart specifications.
- Pandas for CSV inspection, transformation, and result formatting.
- Plotly for interactive charts.
- Streamlit for the complete initial interface.
- pytest for testing.
- Docker Compose for PostgreSQL with pgvector.

Use VS Code and an isolated Conda environment named commerce-agent. Inspect any existing environment before changing it. Choose a mutually compatible Python version and dependency versions, and record them.

Do not substitute MongoDB, FAISS, Chroma, DuckDB, React, Express, or a separate FastAPI service. Streamlit should call Python application services directly.

Do not add a separate agent framework or build a custom LangGraph workflow initially. LangChain’s internal dependencies are acceptable.

The LLM provider/model is not selected. Keep model configuration replaceable through a small adapter. Do not invent API credentials or choose a paid service without asking. Implement everything that does not require the selection first. Clearly distinguish mocked tests from a working live LLM integration.

DATA SOURCE

Use the Brazilian E-Commerce Public Dataset by Olist:
https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce

Expected files:
- olist_orders_dataset.csv
- olist_order_items_dataset.csv
- olist_order_payments_dataset.csv
- olist_products_dataset.csv
- olist_customers_dataset.csv
- olist_sellers_dataset.csv
- olist_order_reviews_dataset.csv
- olist_geolocation_dataset.csv
- product_category_name_translation.csv

Look for these files in the workspace, especially data/raw/.

If unavailable, provide download and placement instructions, and continue implementing the importer, schemas, tools, and tests using clearly labelled small fixtures. Do not present fixtures as real Olist data or claim the full dataset was imported.

Inspect actual source columns before committing to the schema. Preserve historical dates and original BRL currency. Do not replace the dates with current dates or treat the dataset as live.

DATABASE AND INGESTION

Create separate schemas or another clear separation for:
- Imported Olist records.
- Approved analytical views.
- Knowledge documents and embeddings.
- Application history and result references.

The importer should:
- Validate expected files and columns.
- Convert dates and numeric values explicitly.
- Preserve identifiers as strings.
- Handle missing values deliberately.
- Use exact numeric database types for monetary amounts.
- Import in batches or through an appropriate bulk-loading method.
- Be safely repeatable without duplicating records.
- Preserve existing user data; do not drop databases or volumes by default.
- Produce a report of imported rows, rejected rows, duplicates, missing values, and date coverage.
- Add appropriate indexes and validated constraints.

Do not assume every dataset has a simple unique key. Inspect reviews and geolocation especially before assigning uniqueness constraints.

DOCUMENTED METRICS AND ANALYTICAL VIEWS

Define and document:
- Placed order count.
- Delivered order count.
- Item sales value.
- Freight value.
- Recorded payment value.
- Average order value under an explicit definition.
- Delivery duration and late-delivery rate.
- Category-level item sales.
- Payment-method distribution.

Each definition must specify:
- Record grain.
- Included/excluded order statuses.
- Date field used.
- Currency.
- Null handling.
- Relevant denominator and attribution rules.

Do not silently treat placed, approved, and delivered orders as equivalent.

Do not assume the dataset’s timestamps have a confirmed timezone. Inspect documentation and record any unresolved timezone assumption.

Important correctness requirements:
- Count orders distinctly when joins could duplicate them.
- Aggregate payments and items appropriately before joining.
- Never multiply order totals through a many-to-many join.
- Compute category sales from item-level prices.
- Do not assign an entire order’s payment value to each category.
- Distinguish customer_id from customer_unique_id.
- Label payment shares clearly: share of payment records, payment value, or orders using a method. These differ.
- Handle missing categories explicitly.
- Do not infer unavailable refunds, profit, traffic, device, or marketing data.
- Handle zero baselines without invalid percentage changes.
- Treat boundary months and differing period lengths carefully.

Build useful analytical views so common queries are simple and consistently defined. Verify totals against independent calculations on test fixtures.

RAG DESIGN

Use RAG for:
- Schema documentation.
- Relationships and valid join paths.
- Metric definitions.
- Query examples.
- Date coverage and dataset limitations.

Do not embed every transaction for counting or aggregation. PostgreSQL computes the business numbers.

Generate source-grounded documentation from inspected data and explicit project decisions. Do not invent Olist policies or incidents.

Store document/chunk metadata:
- Document ID and title.
- Section.
- Source or provenance.
- Document version.
- Chunk text.
- Embedding model identifier.
- Content hash.

Use Sentence Transformers embeddings and pgvector retrieval. Pick an appropriate small local embedding model after verifying compatibility. Keep the embedding dimension consistent with the database schema. Make ingestion repeatable and handle reindexing when the model or content changes.

Keep approved metric definitions accessible deterministically by ID; essential definitions should not depend solely on similarity search.

AGENT DESIGN

Use one ReAct-style tool-calling agent.

It should:
1. Resolve the requested metrics, dates, filters, and output.
2. Retrieve relevant schema and definitions.
3. Identify the analytical tasks.
4. Call a permitted tool.
5. Inspect the returned evidence.
6. Choose another tool when needed.
7. Verify important numerical claims.
8. Return an answer and optional visualization.
9. Stop when complete or when the configured budget is reached.

Use structured tool calling rather than parsing a hand-written “Thought/Action” transcript.

Show concise task summaries and tool activity in the interface, not private chain-of-thought.

Suggested tools:
- retrieve_context
- inspect_schema
- get_metric_definition
- execute_analytics_query
- check_result
- create_chart

Use configurable tool-call, execution-time, and retry limits. Prevent repeated identical failing calls.

If the user asks “why,” distinguish numerical contributions from causal explanations. Do not claim a cause that the available dataset cannot establish.

QUERY EXECUTION

Allow flexible analytical questions through a constrained query service.

Start by implementing tested analytical functions and approved views. Add model-generated read-only SQL against approved views only when validation and permissions are implemented.

For any generated SQL:
- Validate referenced relations and operations.
- Permit only the intended analytical query scope.
- Reject multiple statements, writes, DDL, COPY, and unapproved functions or relations.
- Do not treat a SELECT prefix or regular expression as sufficient validation.
- If an additional SQL parser dependency is needed, explain it before adding it; otherwise retain the structured-query approach.
- Enforce database-level least privilege.
- Use read-only transactions and statement timeouts.
- Limit returned rows and result payload size.
- Keep import/admin credentials unavailable to the agent.
- Record the executed query and parameters.

Return structured tool results containing:
- Result ID.
- Columns and types.
- Rows.
- Metric definition reference.
- Applied periods and filters.
- Executed query and safe parameters.
- Row-limit/truncation status.
- Warnings and provenance.

Do not silently present a truncated result as the full population.

CHARTING

Plotly must render actual stored query results.

The LLM supplies a Pydantic-validated chart specification, not executable Python:
- Result ID.
- Chart type.
- X/label column.
- Y/value column.
- Optional series/group column.
- Title.
- Units and formatting.

Support:
- Bar.
- Grouped bar.
- Line.
- Pie.
- Scatter.

Validate chart inputs and suitability:
- Line charts require a meaningful ordered axis.
- Pie charts require non-negative parts of a meaningful total.
- Empty results should not generate misleading charts.
- Excessive categories should trigger a better presentation or an explicitly calculated “Other” group.
- Do not invent chart values or accept arbitrary executable code.

Respect explicit user instructions:
- “Do not draw” means no chart.
- “Plot this” should reuse the prior result when possible.
- Changed dates or filters should trigger a new query.
- Preserve axis labels, BRL currency labels, and date ranges.

Provide an Auto / When requested / Never chart preference.

STREAMLIT INTERFACE

Include:
- Chat input and conversation display.
- Dataset date coverage.
- Answer and results table.
- Plotly charts.
- Expandable executed SQL and metric definitions.
- Retrieved-document citations.
- Concise tool activity and status.
- Chart preference.
- Investigation history.
- Clear errors and limitations.

Persist relevant conversation state, result references, and report metadata. Isolate different sessions so results do not leak across users.

Suggested report fields:
- Answer summary.
- Findings with result/document references.
- Tables.
- Chart specifications.
- Metric definitions used.
- Assumptions.
- Limitations.

PROJECT STRUCTURE

Adapt to existing workspace conventions, otherwise use a structure similar to:

src/olist_agent/
    config.py
    db/
    ingestion/
    analytics/
    retrieval/
    agent/
    charts/
    services/
ui/
    app.py
scripts/
data/
    raw/
docs/
tests/
    fixtures/
docker-compose.yml
environment.yml
pyproject.toml
.env.example
.gitignore
README.md

Keep secrets, downloaded raw data, local caches, and generated runtime files out of version control.

Do not overengineer the project with microservices, multiple agents, or unnecessary infrastructure.

TESTING AND ACCEPTANCE

Test meaningful correctness and failure cases:
- Multiple items and multiple payment records do not inflate totals.
- Order counts stay correct after joins.
- Category totals reconcile under matching filters.
- Status and date filters behave correctly.
- Nulls, missing categories, empty results, and zero denominators are handled.
- Disallowed queries are rejected.
- Database permissions restrict writes.
- Chart specifications cannot reference nonexistent columns or result IDs.
- A chart-only follow-up reuses the appropriate prior result.
- Changed filters cause a fresh query.
- Retrieved passages support cited definitions.
- Tool budgets stop unproductive loops.

Create a small evaluation set of questions with independently verified answers. Keep expected answers outside agent-retrievable documents.

Use deterministic model mocks for automated orchestration tests and a separate optional live-model smoke test. Do not claim live LLM functionality was verified through mocks alone.

IMPLEMENTATION SEQUENCE

1. Inspect workspace, instructions, tools, environment, and dataset availability.
2. Create configuration, dependency setup, Docker Compose, and documentation.
3. Implement ingestion and its report.
4. Implement metric definitions, analytical views, and tested tools.
5. Implement document indexing and retrieval.
6. Implement the agent and model adapter.
7. Implement charting and the Streamlit interface.
8. Run available tests and end-to-end checks.
9. Document exact setup and run commands.

Complete each available stage rather than repeatedly asking permission for routine reversible changes.

If blocked by missing data, credentials, Docker, or model selection, continue all independent work and state exactly what remains blocked.

FIRST ACTION

Inspect the current folder and environment. Then start implementing the database foundation, importer, analytical views, and tested query tools.

Record this handoff’s decisions in project documentation.

At each meaningful milestone, explain:
- What was implemented.
- How to run it.
- What was actually tested.
- Any remaining limitations or required input.

Never claim to have installed, run, imported, or verified something unless you actually did.