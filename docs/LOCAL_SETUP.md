# Olist Analytics Agent

Ask natural-language questions over historical Olist commerce data, retrieve metric/schema evidence, run constrained PostgreSQL analyses and render stored results with Plotly. Streamlit calls Python services directly. The initial agent uses LangChain `create_agent`; no custom agent graph or extra web service.

**Current verification:** see [docs/VERIFICATION.md](docs/VERIFICATION.md). The original Olist CSVs have been downloaded and imported into local PostgreSQL: 1,550,922 rows across nine tables, including 99,441 orders, with zero rejected rows. Source-versus-database checks match, and the data survived a graceful server restart. Synthetic fixtures remain separate. Gemini 3.1 Flash-Lite is configured with the user's private local API key.

## Local database on this Windows computer

The native setup uses official PostgreSQL 16.15 binaries and builds pgvector 0.8.6 with the existing Visual Studio C++ compiler. It avoids a Docker/WSL dependency on this computer. Database files live in `.runtime/postgres-data`; preserve that directory.

The `Start` action launches a hidden WMI worker outside the calling terminal or tool process tree and waits for PostgreSQL to accept local connections. The server keeps running after the startup command ends. Server logs are saved in `.runtime/logs/postgres-server.log`.

```powershell
conda activate commerce-agent
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/local_postgres.ps1 -Action Start
python scripts/query_database.py --sql "SELECT COUNT(*) AS orders FROM raw.orders"
python -m streamlit run ui/app.py --server.address 127.0.0.1
```

For SQL clients, use host `127.0.0.1`, port `55432`, database `olist`, username `olist_reader`, and `READER_PASSWORD` from the local `.env` file. [Database access instructions](docs/DATABASE_ACCESS.md) include examples and reader provisioning. SQL queries work independently of the configured Gemini model.

To reproduce the native setup in this workspace (existing data is preserved):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/local_postgres.ps1 -Action Setup
python -m olist_agent.cli init-db
python scripts/download_olist.py
python scripts/inspect_source.py
python -m olist_agent.cli import data/raw
python scripts/configure_reader.py
python scripts/verify_import.py
python -m olist_agent.cli index
```

Stop the native server with the same script and `-Action Stop`. Start it again after a Windows restart with `-Action Start`. Do not run the native server and Docker on the same port simultaneously. Docker Compose remains an alternative below.

## Setup in VS Code / PowerShell with Docker

The native PostgreSQL setup above is already available on this computer. Docker Compose is an alternative for a machine with a working Docker engine. Run from this project directory. Python 3.11 is selected; the inspected existing `commerce-agent` has Python 3.11.16. Preserve that environment and its other projects.

```powershell
conda activate commerce-agent
python -m pip install -e '.[test,gemini]'
code .
python scripts/configure_local.py
docker compose up -d --wait
python -m olist_agent.cli init-db
```

On a machine without this environment, first run `conda env create -f environment.yml`, then activate it. Direct dependency versions are pinned in `pyproject.toml`; [requirements-lock.txt](requirements-lock.txt) records the installed Windows dependency closure. To reproduce that closure: `python -m pip install -r requirements-lock.txt`, then `python -m pip install -e '.[test]'`. Other platforms should resolve the direct pins and regenerate their lock with `python scripts/lock_dependencies.py`.

Local credentials are generated in ignored `.env`, never printed. Admin credentials are used only by CLI bootstrap/import/index commands. Analytics tools receive only the analytics connection; persistence/retrieval uses a separate app role. Docker binds PostgreSQL to localhost port 55432. Existing roles' passwords are aligned with `.env` by `init-db`; this is a local development setup, not a multi-tenant deployment recipe. Never commit `.env` or raw CSVs.

## Data

Run `python scripts/download_olist.py` to download the original public [Olist Kaggle dataset](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) and validate all nine CSV headers before placement in `data/raw/`. The archive/file checksums and source URL are stored in `data/raw/download_manifest.json`. Existing differing files are preserved and cause a clear error. An authenticated manual Kaggle download is an alternative if the public endpoint is unavailable. `python -m olist_agent.cli import data/raw` validates all actual headers before writing, then reports imported/rejected/duplicate rows, missing counts and input date coverage.

```powershell
python -m olist_agent.cli import data/raw
python -m olist_agent.cli coverage
python -m olist_agent.cli index
python -m streamlit run ui/app.py --server.address 127.0.0.1
```

The importer is append-only and repeatable: existing natural keys are preserved, not updated or deleted. An existing key with changed values is still skipped as a duplicate; use a reviewed migration for corrections. Reviews/geolocation preserve identical-row multiplicity via content-hash occurrence keys. Input date coverage includes parsed rows even if a database constraint later rejects them; `coverage` reads accepted orders. Missing non-required values become SQL NULL. Money is exact `NUMERIC(18,2)`; IDs and ZIP prefixes stay strings. Source timestamps stay naive historical values, with unconfirmed timezone. No database or volume is dropped by normal commands.

To try **synthetic fixtures only**, use a fresh local database instead of importing real data:

```powershell
python -m olist_agent.cli import tests/fixtures/raw --fixture
python -m olist_agent.cli index
python -m streamlit run ui/app.py --server.address 127.0.0.1
```

Fixture and real imports cannot be mixed in one database. For a second database, provision it in PostgreSQL and point all three URLs at it, then run `init-db`. Do not delete existing volumes to switch datasets. Fixtures contain four orders and test edge cases; they are not representative population answers.

Indexing downloads `sentence-transformers/all-MiniLM-L6-v2` if uncached. The 384-dimensional embedding contract is checked. Only documentation is embedded; PostgreSQL calculates business totals. Changed content/model is re-embedded, unchanged chunks skipped, stale project chunks removed atomically. Models with another dimension require an explicit database migration. Essential metrics are also available deterministically by ID.

## Model selection

Gemini 3.1 Flash-Lite is the selected API model. It responded successfully with this account's key; Gemini 3.8 was temporarily unavailable and Gemini 3.7 timed out during setup. Install the pinned adapter and configure it using your own key:

```powershell
python -m pip install -e '.[gemini]'
# Add GOOGLE_API_KEY to the local .env file; do not put it in a command or commit it.
python scripts/configure_gemini.py
python -m streamlit run ui/app.py --server.address 127.0.0.1
```

The helper preserves other local settings and existing model selection, sets `MODEL_FACTORY=olist_agent.agent.gemini:create_model`, and defaults to `GEMINI_MODEL=gemini-3.1-flash-lite`. To choose another available Gemini model explicitly, use `python scripts/configure_gemini.py --model MODEL_ID` and restart Streamlit. The adapter uses the Gemini Developer API, low reasoning effort, a 30-second per-request timeout (bounded by the execution budget), zero automatic provider retries and bounded output. Private model thoughts are not requested. PostgreSQL and document embeddings remain local; selected context and tool results are sent to the configured model. Google's free tier has quotas, and one question can make several model calls; the project does not change billing settings. See [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing).

Other providers can still use the same factory contract. Chat is disabled when `MODEL_FACTORY` is empty. Human SQL access through the local query CLI remains available independently. Create an importable adapter for another explicitly chosen provider:

```python
# my_model_adapter.py; implementation depends on your chosen provider
def create_model():
    # Return your provider's LangChain BaseChatModel here.
    # Configure request timeout <= MAX_EXECUTION_SECONDS and bounded provider retries.
    raise NotImplementedError("Choose and configure a provider/model")
```

Set `MODEL_FACTORY=my_model_adapter:create_model` in `.env` to use that alternative adapter. The agent has five Pydantic tools, a validated report, configurable call/time/retry budgets and a recursion ceiling. Asynchronous cancellation bounds orchestration; a synchronous provider or embedding operation in a worker thread can finish after cancellation, so provider request timeouts remain required. PostgreSQL statements have their own timeout. No private chain-of-thought is displayed or persisted.

## Supported analysis and safety

- Placed/delivered order count, item sales, freight, recorded payments, explicitly defined AOV, delivery duration and late-delivery rate.
- Year/month/state breakdowns, category sales and category-by-month comparisons; payment record/value/order shares. Calendar-year filters can select discrete years, so comparing 2016 and 2018 excludes 2017.
- Gemini writes PostgreSQL SELECT SQL from the user question, live approved-view schema and retrieved documentation. SQLGlot validates one read-only query, view/column scope and pure analytical functions before user approval. CTEs, joins, subqueries, window functions and custom calculations are supported within the three approved views.
- Read-only transactions, least-privilege analytics role, bounded row/payload returns and explicit truncation warnings. No totals are silently inferred from truncated output.
- Session-scoped results and history, with database row-level policies for the app role. Use New session and the Sessions selector to keep and switch conversations within the current browser session. Session IDs are browser-session capabilities, not authenticated accounts; reloading loses the capability. Production auth is outside this initial scope.
- Bar/grouped bar/line/pie/scatter specifications use actual stored results. Pie rejects overlapping order shares, undefined/negative/zero totals and truncated populations. Lines require meaningful ordered axes and unique points per series. Too many categories are rejected rather than silently discarded.

The UI contains chat, a **Generate charts** on/off toggle, sessions and history. Asking for graphs or charts turns the toggle on automatically; negative instructions such as “do not draw” override a positive chart request. Each analytics query pauses with the proposed SQL and actual filter values visible. Click **Approve and run** to authorize that exact query, or **Reject** to cancel it. Follow-up and reconciliation queries require their own approval. Multiple queries requested together are reviewed individually before any of that batch runs. Approval is session-bound and single-use; human review time does not consume the active execution budget. Pending approvals are held in memory and disappear when the browser session or server restarts.

For example, “comapare orders of 2016 and 2018 year with help of graph and charts” proposes a placed-order count grouped by year, with the exact requested years filtered and grouped inside Gemini's SQL. It includes all recorded statuses and states unless the question specifies a filter. After approval, the real dataset returns **329 orders in 2016** and **54,011 in 2018**, with a bar chart from those two saved rows. These are totals of available records: the dataset covers only parts of the boundary years, so this comparison does not establish complete annual demand or growth.

Clear, supported calendar questions produce a summary and validated chart directly from approved saved rows, without a second LLM call after the query. A chart-only follow-up reuses the previous approved result without another data query or model call. Other investigations still use the agent for interpretation. If the model fails after an approved query completes, a grounded summary and saved table remain visible in history; requested charts are generated where the stored rows support them. Rejecting an additional query also preserves earlier approved results. Session labels include a number so identical questions remain separately selectable.

Gemini receives the question, recent conversation, actual approved-view columns/types, relationships, metric definitions and any retrieved passages. It writes the complete SQL string in `SQLQuerySpec.sql`; optional title/units/axis fields describe presentation only. Python validates the SQL without generating, rewriting or replacing it. The preview shows the exact model-authored text, and execution runs that same text after approval. Invalid SQL is returned to a non-executing validation tool so Gemini can repair it and request fresh approval. Column metadata is read and cached separately; no business query runs during preview. Legacy QuerySpec templates remain for CLI/fixture calculations and old stored results, and are not exposed as an agent execution tool. Metadata, document retrieval and saved-history reads support the chat separately. Local embedding weights load only when a retrieval tool actually needs them, and then remain cached. The query explorer, coverage panel, raw document/metric/tool panels and extra chart modes have been removed. Reviewed SQL remains accessible in answer history. Reports and supporting evidence persist in PostgreSQL; numeric prose should be reviewed against the displayed table.

See [metric and architecture decisions](docs/DECISIONS.md) and the [authoritative handoff](docs/HANDOFF.md).

## Tests

```powershell
python -m pytest -q
python scripts/embedding_smoke.py
```

Default tests use real LangChain `create_agent` with a deterministic tool-calling mock, real Pydantic/Plotly and Streamlit's app test runner. PostgreSQL tests skip unless explicitly configured; live-model tests skip unless opted in. Expected fixture answers live only in `tests/evaluations.json`, never in the retrieval corpus.

After the native PostgreSQL server or Docker is working and `.env` is loaded:

```powershell
$env:OLIST_TEST_ADMIN_URL = (Get-Content .env | Where-Object { $_ -match '^ADMIN_DATABASE_URL=' }) -replace '^ADMIN_DATABASE_URL=', ''
# init-db loads .env; pytest must also load the local role secrets:
python -c "from dotenv import load_dotenv; load_dotenv(); import pytest; raise SystemExit(pytest.main(['-q','-m','integration']))"
```

Integration tests create a new `olist_test_<uuid>` database and drop only that disposable database afterward. They verify actual SQL totals, evaluation answers, repeat imports, permissions, session isolation and pgvector with mocked vectors (not embedding quality). The configured admin role must be able to create/drop databases and install vector.

Optional **live** smoke, after provider setup, actual import and indexing:

```powershell
$env:OLIST_RUN_LIVE = '1'
python -m pytest -q -m live
```

Do not confuse mocks with live-model verification. Stop the native local server without deleting data with `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/local_postgres.ps1 -Action Stop`. For the Docker alternative, use `docker compose stop`. Docker Desktop cannot currently start on this computer because WSL is unavailable; the native PostgreSQL server provides local database access.
