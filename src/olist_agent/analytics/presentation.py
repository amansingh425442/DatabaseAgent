"""Grounded summaries and charts that do not require another model response."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from olist_agent.analytics.metrics import METRICS
from olist_agent.charts.render import build_chart
from olist_agent.models import ChartSpec, Finding, QueryResult, Report, SQLQuerySpec


_MONEY = {"item_sales", "category_sales", "recorded_payments", "freight", "aov"}


def metric_units(metric_id: str) -> str:
    if metric_id == "custom":
        return "value"
    if metric_id in _MONEY:
        return "BRL"
    if "share" in metric_id or metric_id == "late_delivery_rate":
        return "%"
    return "days" if metric_id == "delivery_days" else "orders"


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def result_title(result):
    definition = METRICS.get(result.metric_id)
    return definition.title if definition else getattr(result.spec, "title", "Query result")


def result_units(result):
    if isinstance(result.spec, SQLQuerySpec) and result.metric_id == "custom":
        return result.spec.units
    return metric_units(result.metric_id)


def value_column(result):
    requested = getattr(result.spec, "y", "value")
    if requested in result.columns:
        return requested
    numeric = [name for name in result.columns if name != "missing_values"
               and result.types.get(name) in ("int", "float", "Decimal")]
    return numeric[0] if len(numeric) == 1 else None


def format_value(value, metric_id, units=None):
    """Exact count formatting and explicit currency/rate units for actual values."""
    number = _number(value)
    if number is None:
        return "undefined (no eligible observations)"
    units = units or metric_units(metric_id)
    if units == "value":
        return f"{number:,f}"
    if units == "orders":
        return f"{number:,.0f} orders" if number == number.to_integral_value() else f"{number:,f} orders"
    rounded = number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"BRL {rounded:,.2f}" if units == "BRL" else f"{rounded:,.2f}{'%' if units == '%' else ' days'}"


def _axis(result):
    explicit = getattr(result.spec, "x", None)
    if explicit in result.columns:
        return explicit
    grouping = result.spec.group_by
    candidates = {"year": "year", "month": "month", "state": "state", "category": "category",
                  "category_month": "month", "payment_method": "payment_method"}
    axis = candidates.get(grouping)
    if axis in result.columns:
        return axis
    if isinstance(result.spec, SQLQuerySpec):
        for name in ("year", "month", "state", "category", "payment_method"):
            if name in result.columns and name != value_column(result):
                return name
        labels = [name for name in result.columns if name != value_column(result)
                  and name != "missing_values" and result.types.get(name) not in ("int", "float", "Decimal")]
        return labels[0] if len(labels) == 1 else None
    return None


def _summary(result):
    title = result_title(result)
    if not result.rows:
        return f"{title}: no matching rows were returned."
    axis = _axis(result)
    value = value_column(result)
    if not axis and len(result.rows) == 1 and value:
        return f"{title}: {format_value(result.rows[0].get(value), result.metric_id, result_units(result))}."
    if not axis or not value:
        return f"{title}: {len(result.rows):,} saved result rows are shown in the table."
    rows = result.rows
    if axis in ("year", "month"):
        rows = sorted(rows, key=lambda row: str(row.get(axis, "")))
    limit = 12 if axis == "year" else 6
    details = []
    for row in rows[:limit]:
        label = str(row.get(axis, "Unknown"))
        if result.spec.group_by == "category_month":
            label += " / " + str(row.get("category", "Unknown"))
        details.append(f"{label}: {format_value(row.get(value), result.metric_id, result_units(result))}")
    suffix = f" Showing the first {limit} of {len(rows)} returned groups; the table contains the remaining rows." if len(rows) > limit else ""
    return f"{title} by {axis.replace('_', ' ')} — " + "; ".join(details) + "." + suffix


def chart_for_result(result: QueryResult, preferred_chart=None):
    """Return a chart verified by the existing renderer, or an honest limitation."""
    axis = _axis(result)
    value = value_column(result)
    if not result.rows:
        return None, "There are no matching rows to plot."
    if not axis or not value:
        return None, "A single total has no comparison axis. Request results by year, month, state, category or payment method to plot a graph."
    if any(_number(row.get(value)) is None for row in result.rows):
        return None, "A graph could not be drawn because some metric values are undefined. The saved table shows the available evidence."
    default = "line" if axis == "month" else "bar"
    kinds = list(dict.fromkeys([preferred_chart, default])) if preferred_chart else [default]
    units = result_units(result)
    failure = None
    for kind in kinds:
        series = getattr(result.spec, "series", None) or ("category" if result.spec.group_by == "category_month" else None)
        if kind == "bar" and series:
            kind = "grouped_bar"
        spec = ChartSpec(result_id=result.result_id, chart_type=kind, x=axis, y=value,
                         series=series, title=result_title(result) + " by " + axis.replace("_", " "), units=units)
        try:
            build_chart(spec, result)
        except (ValueError, TypeError) as exc:
            failure = str(exc)
            continue
        reason = None
        if preferred_chart and kind != preferred_chart and not (preferred_chart == "bar" and kind == "grouped_bar"):
            reason = f"Used a {kind.replace('_', ' ')} chart because the requested chart type cannot accurately represent these results."
        return spec, reason
    return None, "A graph could not be drawn for this result: " + (failure or "no supported comparison axis")


def _limitations(results):
    limitations = []
    for result in results:
        if result.truncated:
            limitations.append("The displayed result is partial; its rows do not represent the complete population.")
        limitations.extend(result.warnings)
        if _axis(result) == "year":
            limitations.append("Calendar-year totals reflect the records available in the dataset. Boundary years may be incomplete, so they do not establish a change in annual demand.")
        years = getattr(result.spec, "years", [])
        if years and result.spec.group_by == "year" and not result.truncated:
            present = {int(row["year"]) for row in result.rows if str(row.get("year", "")).isdigit()}
            missing = [str(year) for year in years if year not in present]
            if missing:
                limitations.append("No result row was returned for " + ", ".join(missing) + "; missing years are not plotted as invented zero values.")
    return list(dict.fromkeys(limitations))


def make_result_report(question, results, permission, preferred_chart=None) -> Report:
    """Construct a report exclusively from already approved, saved query rows."""
    results = list({result.result_id: result for result in results}.values())
    summaries = [_summary(result) for result in results]
    report = Report(answer="\n\n".join(summaries) or "No approved query result is available.",
        findings=[Finding(text=summary, result_ids=[result.result_id]) for result, summary in zip(results, summaries)],
        tables=[result.result_id for result in results],
        metric_ids=list(dict.fromkeys(result.metric_id for result in results)),
        limitations=_limitations(results))
    return ensure_result_charts(report, results, permission, preferred_chart)


def ensure_result_charts(report, results, permission, preferred_chart=None) -> Report:
    """Fill missing graphs from actual results while preserving the given narrative.

    Caller validates existing model-provided chart specs first. This function
    adds only charts validated against the same saved results; it executes no SQL.
    """
    if not permission:
        return report
    results = list(results)
    charted = {spec.result_id for spec in report.charts}
    for result in results:
        if result.result_id in charted:
            continue
        spec, reason = chart_for_result(result, preferred_chart)
        if spec:
            report.charts.append(spec)
            if result.result_id not in report.tables:
                report.tables.append(result.result_id)
        if reason and reason not in report.limitations:
            report.limitations.append(reason)
    report.limitations = list(dict.fromkeys(report.limitations + _limitations(results)))
    return report
