# Query the local Olist database

The original Olist CSVs are imported into PostgreSQL on this computer: 1,550,922
rows across nine tables, including 99,441 orders. People using this computer can
query the data with a SQL client or the Python CLI. These instructions use the
dedicated `olist_reader` account. Both the CLI and browser query explorer were
verified against the populated database.

## Connect a SQL client

Create a PostgreSQL connection in pgAdmin, DBeaver, or another SQL client:

| Setting | Value |
| --- | --- |
| Host | `127.0.0.1` |
| Port | `55432` |
| Database | `olist` |
| Username | `olist_reader` |
| Password | `READER_PASSWORD` in this project's local `.env` file |

Copy the password value without its surrounding quotes.

Keep `.env` private; it also contains administrative credentials. Use the reader
account when sharing access with other people on this computer. The database
listens on the local computer; this connection does not provide remote access.

The reader can select from all nine tables in `raw` and the approved views in
`analytics`. Its transactions default to read only, and queries have a 10-second
statement timeout. It has no write access to imported data, application history,
or the document index.

For example, run these statements individually in your SQL editor:

```sql
SELECT table_schema, table_name
FROM information_schema.tables
WHERE table_schema IN ('raw', 'analytics')
ORDER BY table_schema, table_name;
```

```sql
SELECT order_status, COUNT(*) AS orders
FROM raw.orders
GROUP BY order_status
ORDER BY orders DESC;
```

```sql
SELECT DATE_TRUNC('month', order_purchase_timestamp)::date AS month,
       COUNT(*) AS orders
FROM raw.orders
GROUP BY 1
ORDER BY 1;
```

## Query from PowerShell

Use the existing Python environment from the project directory:

```powershell
conda activate commerce-agent
python scripts/query_database.py --sql "SELECT COUNT(*) AS orders FROM raw.orders"
python scripts/query_database.py --sql "SELECT * FROM raw.orders ORDER BY order_purchase_timestamp LIMIT 10"
```

The native setup also includes PostgreSQL's interactive SQL terminal:

```powershell
& '.runtime/postgres/pgsql/bin/psql.exe' -h 127.0.0.1 -p 55432 -U olist_reader -d olist -W
```

Enter `READER_PASSWORD` from `.env` when prompted. Type `\dt raw.*` to list
imported tables, run a query ending in `;`, and use `\q` to exit. No password is
included in the command line.

To run a longer query, save one `SELECT` or row-producing `WITH` statement to a
UTF-8 SQL file, then run:

```powershell
python scripts/query_database.py --file query.sql
```

The CLI uses a read-only transaction and returns at most 500 rows. It prints
`TRUNCATED` when more rows match. Use `--limit 50` to reduce the display limit,
and use SQL filters or aggregation for complete totals. The 500-row display cap
belongs to this CLI; SQL clients manage their own result limits.

This CLI is for SQL written by a person. It is separate from the analytics
agent, which continues to use validated query specifications and approved,
parameterized SQL. Model-generated SQL execution remains disabled. SQL clients
and this CLI work without choosing an LLM provider or configuring chat.

## Provision or repair the reader

After PostgreSQL is running and the project schemas are initialized, run:

```powershell
python scripts/configure_reader.py
```

This uses `ADMIN_DATABASE_URL` from `.env`, creates the dedicated reader, and
stores `READER_PASSWORD` and `READER_DATABASE_URL` in that same file. Existing
reader credentials are reused. Other `.env` settings and comments are preserved;
credentials are never printed. It grants SELECT on current `raw` and `analytics`
tables/views and on future tables created there by the configured admin role.

If connection fails, check that the local PostgreSQL process is running on port
55432 and that the reader settings in `.env` target the `olist` database. If the
CLI reports a timeout, narrow the date range, select fewer columns, or aggregate
the results before displaying them.
