from datetime import date
import pandas as pd
import plotly.express as px
from olist_agent.models import ChartSpec, SQLQuerySpec


def build_chart(spec: ChartSpec, result):
    if spec.result_id != result.result_id:
        raise ValueError("Chart result ID mismatch")
    if not result.rows:
        raise ValueError("Cannot chart empty results")
    for column in (spec.x, spec.y, spec.series):
        if column and column not in result.columns:
            raise ValueError(f"Unknown chart column: {column}")
    if spec.x == spec.y:
        raise ValueError("Label and value columns must differ")
    if spec.y != "value" and not isinstance(result.spec, SQLQuerySpec):
        raise ValueError("Use the metric value column; diagnostic columns are not the requested metric")
    if spec.series in (spec.x, spec.y):
        raise ValueError("Series must differ from label and value columns")
    monetary = result.metric_id in ("item_sales", "category_sales", "recorded_payments", "freight", "aov") or (isinstance(result.spec, SQLQuerySpec) and result.spec.units == "BRL")
    expected_units = "BRL" if monetary else "%" if "share" in result.metric_id or result.metric_id == "late_delivery_rate" else "days" if result.metric_id == "delivery_days" else "orders"
    if isinstance(result.spec, SQLQuerySpec) and result.metric_id == "custom":
        expected_units = result.spec.units
    if spec.units != expected_units:
        raise ValueError(f"Metric requires units {expected_units}")
    frame = pd.DataFrame(result.rows)
    try:
        frame[spec.y] = pd.to_numeric(frame[spec.y], errors="raise")
    except (ValueError, TypeError):
        raise ValueError("Chart value column must be numeric") from None
    if frame[spec.y].isna().any():
        raise ValueError("Cannot plot undefined values; select a result with eligible observations")
    if frame[spec.x].nunique() > 30 and spec.chart_type in ("bar", "grouped_bar", "pie"):
        raise ValueError("Too many categories; request a narrower query (no silent top-N or Other)")
    if spec.chart_type == "grouped_bar" and not spec.series:
        raise ValueError("Grouped bar requires a series column")
    suffix = " (partial result)" if result.truncated else ""
    title = spec.title + suffix
    years = result.spec.years
    if isinstance(result.spec, SQLQuerySpec) and spec.x == "year":
        years = sorted({int(row["year"]) for row in result.rows})
    if years:
        title += " | Years: " + ", ".join(str(year) for year in years)
    whole_years = bool(years and result.spec.start == date(min(years), 1, 1)
                      and result.spec.end == date(max(years) + 1, 1, 1))
    if (result.spec.start or result.spec.end) and not whole_years:
        title += f" | {result.spec.start or 'coverage start'} to {result.spec.end or 'coverage end'} (end exclusive)"
    kwargs = dict(data_frame=frame, x=spec.x, y=spec.y, title=title,
                  labels={spec.y: expected_units, spec.x: spec.x.replace('_', ' ').title()})
    if spec.series:
        kwargs["color"] = spec.series
    if spec.chart_type == "pie":
        if result.truncated or result.metric_id in ("custom", "payment_order_share", "aov", "delivery_days", "late_delivery_rate"):
            raise ValueError("Pie requires complete, mutually exclusive additive parts")
        if spec.series or frame[spec.x].duplicated().any():
            raise ValueError("Pie labels must be unique with no series")
        if (frame[spec.y] < 0).any() or frame[spec.y].sum() <= 0:
            raise ValueError("Pie requires a positive total and nonnegative values")
        figure = px.pie(frame, names=spec.x, values=spec.y, title=title)
    elif spec.chart_type == "line":
        if frame.duplicated([spec.x] + ([spec.series] if spec.series else [])).any():
            raise ValueError("Line requires one value per axis point and series; specify series for category comparisons")
        if spec.x == "month":
            frame[spec.x] = pd.to_datetime(frame[spec.x], errors="raise")
        elif result.types.get(spec.x) not in ("int", "float", "Decimal", "date", "datetime"):
            raise ValueError("Line charts require an ordered numeric or temporal axis")
        kwargs["data_frame"] = frame.sort_values(spec.x)
        figure = px.line(**kwargs, markers=True)
    elif spec.chart_type == "scatter":
        if result.types.get(spec.x) not in ("int", "float", "Decimal"):
            raise ValueError("Scatter requires a numeric X axis")
        frame[spec.x] = pd.to_numeric(frame[spec.x], errors="raise")
        figure = px.scatter(**kwargs)
    else:
        if spec.x == "year":
            frame[spec.x] = frame[spec.x].astype(str)
            kwargs["data_frame"] = frame.sort_values(spec.x)
        figure = px.bar(**kwargs, barmode="group" if spec.chart_type == "grouped_bar" else "relative")
        if spec.x == "year":
            figure.update_traces(texttemplate="%{y:,}", textposition="outside", cliponaxis=False)
            figure.update_xaxes(type="category", title="Year")
    if monetary:
        figure.update_layout(yaxis_tickprefix="R$ ")
    if expected_units == "%" and spec.chart_type != "pie":
        figure.update_layout(yaxis_ticksuffix="%")
    figure.update_layout(template="plotly_white")
    return figure
