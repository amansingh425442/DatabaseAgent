# Verification record — 5 October 2026

## Real dataset and local database

Downloaded the original public [Olist dataset](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) through Kaggle's HTTPS dataset endpoint. The ZIP is 44,717,580 bytes; SHA256 is `967e41e04fc306fe604e2a693f488995a8b41e5047418f8a5c8e4abd6deca784`. All nine actual CSV headers matched the importer contract before import. The ignored `data/raw/download_manifest.json` records the source and individual file hashes.

The actual import committed **1,550,922 rows, zero rejected rows and zero already-present keys skipped**, explicitly labelled `olist`. Original repeated geolocation rows are retained through occurrence keys; 261,831 identical repeated rows are source records, not import failures. Repeated review identifiers are preserved. Fixtures remain separate.

| Table | CSV rows | PostgreSQL rows |
| --- | ---: | ---: |
| raw.customers | 99,441 | 99,441 |
| raw.sellers | 3,095 | 3,095 |
| raw.category_translation | 71 | 71 |
| raw.products | 32,951 | 32,951 |
| raw.orders | 99,441 | 99,441 |
| raw.order_items | 112,650 | 112,650 |
| raw.order_payments | 103,886 | 103,886 |
| raw.order_reviews | 99,224 | 99,224 |
| raw.geolocation | 1,000,163 | 1,000,163 |

Installed native **PostgreSQL 16.15 with pgvector 0.8.6** under `.runtime`, using official EDB binaries and official pgvector source, compiled with the existing Visual Studio C++ tools. Source URLs, versions, compiler and archive checksums are recorded in `.runtime/postgres-provenance.json`. Docker Desktop could not start because WSL was unavailable; the native server provides the required PostgreSQL stack. It uses SCRAM authentication and listens only on `127.0.0.1:55432`. Database files remain in `.runtime/postgres-data`.

A graceful server stop/start preserved the imported data. After the final controlled restart, both Stop and the default Start action exited successfully, and an actual database query returned 99,441 orders, 24 document chunks and installed vector version 0.8.6. Start launches a hidden WMI worker outside the calling tool/terminal process tree; no Windows service, scheduled task or global security setting was changed. The local helper preserves existing clusters and loaded extension files. The server was also recovered after tool-session termination, again preserving the data. The reader CLI still returned 99,441 orders after the final restart, and the Streamlit health endpoint returned `ok`.

## Independent source checks

`scripts/inspect_source.py` inspected all actual CSV types, dates and repetition. `scripts/verify_import.py` compared every table count and independent CSV arithmetic against PostgreSQL, using the real analytics and app roles for query/result persistence. All checks passed; the report is `.runtime/import-verification.json`.

- Placed orders: **99,441**; delivered orders: **96,478**.
- Recorded item sales: **BRL 13,591,643.70**; freight: **BRL 2,251,909.54**.
- Recorded payments: **BRL 16,008,872.12**.
- All twelve monthly order counts for 2017 matched independent source grouping.
- Category item prices reconciled exactly to total source item sales; payment amounts are not attributed to categories.
- Accepted purchase-date coverage: **2016-09-04 21:15:19 through 2018-10-17 17:30:18**. Source timezone remains unconfirmed; no conversion is claimed.

## Application checks

- Offline suite: **41 passed, 7 deselected** with `-m 'not integration and not live'`. These cover contract/type validation, metric arithmetic, independent fixture expected answers, structured-query constraints, chart semantics, bounded payloads, session isolation, actual LangChain `create_agent` orchestration with a deterministic mock, tool budgets and Streamlit's disconnected state.
- Real local Sentence Transformers smoke passed: all-MiniLM-L6-v2 produced two 384-dimensional embeddings. This result alone does not prove database retrieval.
- Dependency check passed with no broken requirements; Python compilation and CLI help passed. Required direct versions and installed closure are recorded in project configuration and requirements-lock.txt.
- Populated browser smoke passed using Playwright with Microsoft Edge. Streamlit at `http://127.0.0.1:8501` displayed **99,441 recorded orders**, submitted the query explorer's placed-order metric, and rendered the persisted result **99,441** with real `olist` provenance. Title, controls and no-provider notice were verified; screenshot `.runtime/ui.png` was inspected. The UI runs as a hidden background process started through WMI.
- Dedicated `olist_reader` credentials are stored only in ignored `.env`. Actual CLI order count returned **99,441**; all twelve 2017 monthly counts matched the independent CSV verification (45,101 orders). All six elevated role flags were false, transactions defaulted to read only, and the 10-second timeout was configured. SELECT-only permissions cover all nine raw tables, three analytics views and future admin-created objects in those schemas.
- Eight rollback-only reader probes denied private app/index reads, raw updates, raw/public table creation, schema creation and admin role switching, including permanent writes attempted with read-only mode overridden. The 500-row cap, explicit truncation, `--limit 5` and literal `%` LIKE queries passed. No source data was changed; no credentials were printed.

Real PostgreSQL integration suite: **6 passed, 42 deselected in 13.82 seconds**. A fresh disposable database verified exact totals, status/date/empty-result behavior, independent fixture evaluations, repeat import with source multiplicity, analytics permissions, app RLS and actual pgvector storage/retrieval with mocked embeddings. The disposable database was removed; the real imported database was preserved. JUnit output is `.runtime/postgres-tests.xml`.

Actual local model indexing/retrieval passed separately: Sentence Transformers `all-MiniLM-L6-v2` encoded **24 source-grounded documentation chunks, each with 384 dimensions**, persisted them in the populated database, and the app role retrieved five passages through pgvector cosine search. Results included the placed-order metric, join paths and structured examples. The report is `.runtime/retrieval-verification.json`. This verifies the actual local embedding and PostgreSQL retrieval path, independently of the integration test's mocked vectors.

## Gemini connection

The user supplied `GOOGLE_API_KEY` in the private local `.env` file. An authenticated Gemini model-list request succeeded and included Gemini 3.8 Flash. Installed the pinned `langchain-google-genai==4.4.0` adapter with the existing dependency versions constrained; `pip check` passed. The installed closure now records 113 runtime/test/provider dependencies. No credentials were printed or committed.

The adapter explicitly selects the Gemini Developer API, uses bounded output and a 30-second per-request timeout, disables automatic provider retries and requests no model thoughts. The model factory is configured and Streamlit was restarted to load it. Browser verification confirmed the populated database and enabled conversational input; `.runtime/ui-gemini.png` records the page. Local embeddings are cached across browser questions.

Gemini 3.8 returned provider **HTTP 503 UNAVAILABLE / high demand**, and Gemini 3.7 reached its request deadline. Gemini 2.5 Flash returned a provider retirement/access error for this account. An authenticated Gemini 3.1 Flash-Lite generation probe succeeded; it is now selected with low reasoning effort. Google's [pricing page](https://ai.google.dev/gemini-api/docs/pricing) lists a free tier for this model; no billing settings were changed.

An actual **Gemini 3.1 Flash-Lite tool-agent run** called `execute_analytics_query`, produced a real session-scoped query result, and answered **99,441 orders**, matching PostgreSQL coverage. Its stored finding cited the actual result ID. The model omitted `Report.tables`; the first strict smoke therefore failed its table-display assertion despite the correct SQL result and answer. The service now includes validated result IDs cited by findings/charts in the table list before persistence and rendering. This uses existing session/evidence validation and does not fabricate a result. A deterministic regression covers this observed omission.

The post-fix **LIVE browser chat check passed**: submitted “How many placed orders are recorded in the database? Do not draw.” through the chat send button, received **99,441 orders**, displayed the supporting PostgreSQL table and persisted the report with real executed-tool activity. The final page screenshot is `.runtime/ui-gemini.png`. Stored evidence was independently checked under the app role against analytics coverage; `.runtime/gemini-verification.json` records the verified report. This is actual Gemini API execution, not a scripted mock. Enter-key automation did not submit reliably in the initial browser attempt; the successful test used the visible send button.

The app explains provider availability, quota, authentication, model-selection and timeout failures without reflecting raw error payloads. Targeted model/agent/UI suite: **14 passed in 11.84 seconds**, including evidence-table normalization and five credential-safe error cases. Live testing establishes the tested question's behavior; other questions still need their displayed evidence reviewed.

Human SQL and SQL clients continue to work independently of model availability. The query explorer described in earlier browser checks has since been removed from the simplified UI.

See [DATABASE_ACCESS.md](DATABASE_ACCESS.md) for connection settings and query examples. README includes the local start commands.

## Simplified UI and query approval

The query explorer, dataset coverage, advanced chart modes, raw document/metric panels and tool activity panels were removed. The UI now contains chat, a Generate charts toggle, numbered sessions, question history, proposed SQL and explicit Approve and run / Reject controls. Actual filter values are visible in SQL before execution. Reviewed SQL stays accessible in history.

- Offline suite: **73 passed, 8 deselected in 28.37 seconds**. Includes 26 agent and 9 Streamlit UI tests exercising the real create_agent orchestration with scripted models. Coverage includes zero execution before approval, rejection, session-bound/single-use decisions, multiple individually reviewed queries, fresh approval for follow-ups, immutable previews, changed-limit invalidation and excluding human review time from the active budget.
- Real PostgreSQL suite: **7 passed in 27.03 seconds** in a fresh disposable database. The new SQLAlchemy statement trace proves preview makes no database call and execution performs exactly one business-data read, with the same SQL and parameters shown for approval. Existing permissions, RLS, metrics, import and vector checks still passed.
- UI interactions verify sessions retain pending decisions and isolated history, chart permission persists independently, duplicate questions remain separately selectable, and enabling charts renders a validated Plotly chart only after approval.
- Recovery tests verify an API failure or invalid model report after a query cannot discard approved, saved tables. They also verify rejecting a later query preserves earlier approved evidence. Raw provider payloads and credentials are not shown.

The actual Gemini/Edge browser path displayed proposed SQL and created **zero results before approval and zero after rejection**. A later explicitly approved whole-dataset query created exactly one saved result containing **99,441 orders**, displayed its table and saved it in history. The AI explanation did not complete, so the deterministic recovery notice and actual table were shown. The strict browser smoke expected numeric prose and failed on the recovery notice; the saved table/count, scope and executed/rejected review states were then independently verified. Session/history navigation and chart rendering are covered separately by the Streamlit interaction tests.

Evidence: `.runtime/approval-verification.json`, `.runtime/ui-query-approval.png`, and `.runtime/ui-approved-result.png`. These checks do not claim that every future model answer will complete; approved results remain accessible when it does not.

## Year-comparison scope and graph correction

The user's screenshot question, “comapare orders of 2016 and 2018 year with help of graph and charts”, previously returned the incorrect all-time total. It now produces a placed-order count grouped by calendar year, with discrete `years=[2016,2018]`, start `2016-01-01` and exclusive end `2019-01-01`. The discrete year predicate excludes 2017; no state or status filter is added unless requested. Integer year parameters and chronological ordering are covered by compilation tests and real PostgreSQL boundary tests.

The standard create_agent workflow now preserves clear supported calendar scope before the existing human approval middleware. A wrong all-time/SP proposal or an attempted reuse of a previous 99,441-order answer becomes a fresh correctly scoped SQL preview. No query runs until approval. Once approved rows are saved, a deterministic summary and validated chart finish clear scoped calendar questions without a second model/API call. Chart-only follow-ups reuse approved saved rows without another data query or model call. Positive graph/chart words turn the UI toggle on, explicit negatives override them, and embeddings load only if retrieval actually needs them.

The actual **Gemini API + Microsoft Edge + local PostgreSQL** smoke passed for the screenshot question:

- The SQL review was visible with **zero query results before approval**.
- Approving created exactly **one** saved query result: **2016: 329 orders; 2018: 54,011 orders**.
- Both values matched independent grouping of the original Olist orders CSV.
- The visible Plotly chart used x values **2016, 2018** and y values **329, 54,011**, matching the saved table.
- The summary and chart completed without a post-query model call, and the answer was saved in history.

Evidence: `.runtime/year-comparison-verification.json`, `.runtime/ui-year-query-review.png` and `.runtime/ui-year-comparison.png`. The verification JSON records the reviewed SQL, actual stored rows, source counts and plotted coordinates. This is an actual Gemini/Edge/database check, separate from scripted model tests.

Final offline suite: **160 passed, 9 deselected in 72.82 seconds**, including the last five intent-guard regressions. The real PostgreSQL integration suite passed **8 tests** in a fresh disposable database, including noncontiguous year selection and exact preview/execution agreement. Six focused year-flow and Streamlit regressions also passed, covering the screenshot typo, corrected scope before approval, rejection, previous-result scope changes, chart enablement and Plotly rendering. The actual Gemini/Edge/PostgreSQL browser smoke passed again after the final server restart, and the updated result screenshot was visually inspected.

These counts compare available historical records. Purchase coverage starts on **4 September 2016** and ends on **17 October 2018**; both comparison years have partial coverage. The answer discloses that the totals do not establish complete annual demand or annual growth. No missing year is represented as an invented zero.

## Gemini-authored SQL — 6 October 2026

At the user's request, the agent now receives the live approved-view column schema, types, relationships and metric documentation and writes complete PostgreSQL SQL in `SQLQuerySpec.sql`. Python validates and executes that exact text; it does not compile a QuerySpec, insert missing filters or replace model SQL with a template. The agent's old ScopeMiddleware is removed. SQLQuerySpec contains SQL plus optional presentation labels/units/axes, not separate SQL filter/grouping fields. Legacy templates remain for direct CLI/fixture analysis and historical results only.

- Final offline suite: **197 passed, 10 deselected in 35.31 seconds**. The new tests cover SELECT/CTE/join/window queries, unknown/ambiguous columns, forbidden relations and side effects, writes in CTEs, SELECT INTO, locks, multi-statements, recursive queries, custom output charts and exact SQL preservation. A runtime regression makes `compile_query` raise and proves model-authored SQL still reaches approval/execution. Another proves invalid SQL returns non-executing feedback to the model and its repaired SQL still waits for approval.
- Actual PostgreSQL integration suite: **9 passed, 198 deselected in 15.14 seconds**, using a disposable database. The generated-SQL test verifies live column metadata, an unchanged CTE/window query, literal percent signs and colons, exact stored SQL, unknown/private relation rejection and streamed row truncation. Existing import, metric, role, RLS and vector tests still pass.
- The actual **Gemini + Edge + local PostgreSQL** browser check passed after restarting the owned app process. Gemini generated `SELECT EXTRACT(YEAR FROM order_purchase_timestamp) AS year, COUNT(DISTINCT order_id) AS value FROM analytics.order_facts WHERE EXTRACT(YEAR FROM order_purchase_timestamp) IN (2016, 2018) GROUP BY 1 ORDER BY 1 ASC`. There were zero new results before approval, exactly one afterward, and the stored query equalled the SQL shown for review. Rows and Plotly coordinates were **2016:329; 2018:54,011**, matching independent original-CSV counts. The summary/chart and history were saved, and the final screenshot was visually inspected.
- Documentation was reindexed locally: **27 chunks, 4 changed chunks embedded**, using all-MiniLM-L6-v2 and 384-dimensional pgvector storage. The retrieval corpus now includes SQL-text examples and approved-view column documentation. SQLGlot **30.21.0** is pinned; the dependency closure records **114** packages and `pip check` passed.

Evidence: `.runtime/gemini-generated-sql-verification.json`, `.runtime/ui-year-query-review.png` and `.runtime/ui-year-comparison.png`. The final app health endpoint returned `ok`. SQL validation constrains syntax, accessible relations/columns and permitted functions; it cannot prove that every model SQL query correctly interprets a natural-language request. The user reviews the exact SQL before execution, and partial historical-year coverage is disclosed.
