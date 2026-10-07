"""Validate model SQL without rewriting it or executing a preview."""
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, OptimizeError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope, Scope
from olist_agent.analytics.schema_context import APPROVED_COLUMNS

# Pure analytical functions only. Database access, files, configuration, advisory
# locks and sleep functions are never available through the generated-SQL tool.
_FUNCTIONS = {
    "COUNT", "SUM", "AVG", "MIN", "MAX", "COALESCE", "NULLIF", "CAST", "TRY_CAST",
    "ABS", "ROUND", "CEIL", "CEILING", "FLOOR", "POWER", "SQRT", "MOD", "GREATEST", "LEAST",
    "EXTRACT", "DATE_TRUNC", "TIMESTAMP_TRUNC", "DATE", "DATE_PART", "DATE_DIFF", "DATEDIFF",
    "TO_CHAR", "TIME_TO_STR", "STR_TO_TIME", "TO_DATE", "TO_TIMESTAMP",
    "LOWER", "UPPER", "LENGTH", "CHAR_LENGTH", "TRIM", "LTRIM", "RTRIM", "SUBSTRING",
    "SUBSTR", "REPLACE", "CONCAT", "CONCAT_WS", "SPLIT_PART", "STR_POSITION", "POSITION",
    "ROW_NUMBER", "RANK", "DENSE_RANK", "PERCENT_RANK", "CUME_DIST", "NTILE", "LAG", "LEAD",
    "FIRST_VALUE", "LAST_VALUE", "NTH_VALUE", "STDDEV", "STDDEV_SAMP", "STDDEV_POP",
    "VARIANCE", "VAR_SAMP", "VAR_POP", "BOOL_AND", "BOOL_OR", "STRING_AGG", "GROUP_CONCAT",
    "ARRAY_AGG", "PERCENTILE_CONT", "PERCENTILE_DISC", "FILTER", "CASE", "IF", "AND", "OR", "NOT",
}
_TYPES = {"INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "DECIMAL", "NUMERIC", "DOUBLE",
          "FLOAT", "REAL", "TEXT", "VARCHAR", "CHAR", "DATE", "TIME", "TIMESTAMP",
          "TIMESTAMPTZ", "TIMESTAMPNTZ", "TIMESTAMPLTZ", "INTERVAL", "BOOLEAN", "UNKNOWN"}


def validate_sql(sql: str, columns=None):
    """Return an AST for one bounded-size SELECT over approved views only."""
    if not isinstance(sql, str) or not sql.strip() or len(sql) > 20000:
        raise ValueError("SQL must contain 1–20000 characters")
    try:
        statements = sqlglot.parse(sql, read="postgres", error_level="RAISE")
    except (sqlglot.errors.SqlglotError, RecursionError):
        raise ValueError("SQL is not a valid PostgreSQL SELECT statement") from None
    if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise ValueError("Only one SELECT query is allowed; writes and multiple statements are forbidden")
    tree = statements[0]
    if len(list(tree.walk())) > 2500:
        raise ValueError("SQL is too complex; use a smaller analytical query")
    forbidden = (exp.DML, exp.DDL, exp.Command, exp.Into, exp.Lock, exp.Parameter,
                 exp.Placeholder, exp.Dot, exp.TableSample)
    for node in tree.walk():
        if isinstance(node, forbidden):
            raise ValueError("SQL contains a forbidden write, lock, parameter or qualified function")
        if isinstance(node, exp.With) and node.args.get("recursive"):
            raise ValueError("Recursive queries are not supported")
        if isinstance(node, exp.DataType) and getattr(node.this, "value", str(node.this)).upper() not in _TYPES:
            raise ValueError("SQL uses an unsupported cast type")
        if isinstance(node, exp.Func):
            name = node.name.upper() if isinstance(node, exp.Anonymous) else node.sql_name().upper()
            if name not in _FUNCTIONS:
                raise ValueError(f"SQL function {name} is not allowed")
    schema = columns or APPROVED_COLUMNS
    try:
        physical = []
        scopes = traverse_scope(tree)
        for scope in scopes:
            for _, source in scope.selected_sources.values():
                if isinstance(source, Scope):
                    continue  # CTE/subquery resolved in its own lexical scope.
                if not isinstance(source, exp.Table) or not isinstance(source.this, exp.Identifier):
                    raise ValueError("Only approved views, SELECT subqueries and CTEs are allowed")
                if source.catalog or source.db != "analytics" or source.name not in schema:
                    raise ValueError("SQL can read only explicitly qualified approved analytics views")
                physical.append(source)
        # Unused CTEs also need validation; do not rely only on selected sources.
        resolved = {id(table) for table in physical}
        for table in tree.find_all(exp.Table):
            if table.db or table.catalog:
                if id(table) not in resolved or table.catalog or table.db != "analytics" or table.name not in schema:
                    raise ValueError("SQL references a forbidden relation")
        if not physical:
            raise ValueError("SQL must read at least one approved analytics view")
        qualify(tree.copy(), dialect="postgres", schema={"analytics": schema},
                validate_qualify_columns=True, quote_identifiers=False)
    except (OptimizeError, sqlglot.errors.SchemaError):
        raise ValueError("SQL references an unknown or ambiguous column; check the supplied schema") from None
    return tree


def preview_sql(spec, max_rows=500, columns=None):
    validate_sql(spec.sql, columns)
    # The reviewed and executed SQL are exactly the model's text. Row limits
    # bound fetching; neither SQL generation nor SQL rewriting happens here.
    return {"spec": spec.model_dump(mode="json"), "query": spec.sql,
            "display_query": spec.sql, "parameters": {}, "max_rows": max_rows}
