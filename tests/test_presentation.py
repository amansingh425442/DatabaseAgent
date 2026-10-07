from decimal import Decimal

import pytest

from olist_agent.analytics.presentation import (chart_for_result, ensure_result_charts,
                                               format_value, make_result_report)
from olist_agent.charts.render import build_chart
from olist_agent.models import QueryResult, QuerySpec, Report


def result(group="year", metric="placed_orders", rows=None, **kwargs):
    axis = {"total": None, "year": "year", "month": "month", "state": "state", "category": "category",
            "category_month": "month", "payment_method": "payment_method"}[group]
    columns = [axis, "value"] if axis else ["value"]
    if group == "category_month":
        columns.insert(1, "category")
    types = {"value": "Decimal", "year": "int", "month": "date", "state": "str", "category": "str", "payment_method": "str"}
    return QueryResult(columns=columns, types=types, rows=rows if rows is not None else [{"year": 2016, "value": 329}, {"year": 2018, "value": 54011}],
        metric_id=metric, spec=QuerySpec(metric=metric, group_by=group, years=[2016, 2018] if group == "year" else []),
        query="TEST SQL", parameters={}, provenance="test saved evidence", **kwargs)


def test_year_comparison_survives_model_failure_with_real_numbers_and_bar_chart():
    data = result()
    report = make_result_report("compare orders 2016 and 2018 with graphs", [data], True)
    assert "2016: 329 orders" in report.answer and "2018: 54,011 orders" in report.answer
    assert "99,441" not in report.answer
    assert report.tables == [data.result_id]
    assert report.findings[0].result_ids == [data.result_id]
    assert len(report.charts) == 1
    figure = build_chart(report.charts[0], data)
    assert report.charts[0].chart_type == "bar"
    assert [str(year) for year in figure.data[0].x] == ["2016", "2018"]
    assert list(figure.data[0].y) == [329, 54011]
    assert any("Boundary years may be incomplete" in message for message in report.limitations)


def test_month_chart_is_temporal_line_and_financial_values_keep_currency():
    data = result(group="month", metric="item_sales", rows=[{"month": "2017-02-01", "value": "12.25"}, {"month": "2017-01-01", "value": "9.50"}])
    report = make_result_report("monthly item sales", [data], True)
    assert report.charts[0].chart_type == "line" and report.charts[0].units == "BRL"
    figure = build_chart(report.charts[0], data)
    assert list(figure.data[0].y) == [9.5, 12.25]
    assert "BRL 12.25" in report.answer and "BRL 9.50" in report.answer


@pytest.mark.parametrize("value,metric,expected", [
    (99441, "placed_orders", "99,441 orders"),
    (Decimal("1.025"), "aov", "BRL 1.03"),
    ("0.105", "late_delivery_rate", "0.11%"),
    ("3.1415", "delivery_days", "3.14 days"),
    (0, "placed_orders", "0 orders"),
    (None, "delivery_days", "undefined (no eligible observations)"),
    ("NaN", "aov", "undefined (no eligible observations)"),
])
def test_values_use_metric_units_and_preserve_undefined_or_zero(value, metric, expected):
    assert format_value(value, metric) == expected


def test_chart_denial_is_respected_for_fallback_report():
    report = make_result_report("no charts", [result()], False)
    assert report.charts == []


def test_empty_result_has_no_invented_zero_or_graph():
    report = make_result_report("compare years", [result(rows=[])], True)
    assert "no matching rows" in report.answer
    assert not report.charts
    assert any("no matching rows to plot" in warning for warning in report.limitations)


def test_missing_year_is_not_padded_with_an_invented_zero():
    data = result(rows=[{"year": 2018, "value": 7}])
    report = make_result_report("compare", [data], True)
    assert "2016: 0" not in report.answer
    assert any("No result row was returned for 2016" in warning for warning in report.limitations)
    assert [str(year) for year in build_chart(report.charts[0], data).data[0].x] == ["2018"]


def test_undefined_average_is_not_silently_plotted_as_zero():
    report = make_result_report("monthly average", [result(group="month", metric="aov", rows=[{"month": "2017-01-01", "value": None}])], True)
    assert "undefined" in report.answer
    assert not report.charts
    assert any("undefined" in warning for warning in report.limitations)


def test_overlapping_payment_order_share_pie_falls_back_to_bar():
    data = result(group="payment_method", metric="payment_order_share", rows=[{"payment_method": "card", "value": "90"}, {"payment_method": "voucher", "value": "20"}])
    report = make_result_report("pie", [data], True, preferred_chart="pie")
    assert len(report.charts) == 1 and report.charts[0].chart_type == "bar"
    assert any("requested chart type" in warning for warning in report.limitations)
    assert sum(build_chart(report.charts[0], data).data[0].y) == 110


def test_truncated_chart_carries_partial_result_warning_and_avoids_pie():
    data = result(group="state", rows=[{"state": "SP", "value": 12}], truncated=True)
    report = make_result_report("pie", [data], True, preferred_chart="pie")
    assert report.charts[0].chart_type == "bar"
    assert "partial result" in build_chart(report.charts[0], data).layout.title.text
    assert any("do not represent the complete population" in warning for warning in report.limitations)


def test_requested_chart_added_when_model_only_returned_text_and_table():
    data = result()
    original = Report(answer="A valid narrative.", tables=[data.result_id])
    repaired = ensure_result_charts(original, [data], True)
    assert repaired.answer == "A valid narrative."
    assert len(repaired.charts) == 1
    repaired = ensure_result_charts(repaired, [data], True)
    assert len(repaired.charts) == 1


def test_single_total_explains_why_it_is_not_a_comparison_chart():
    data = result(group="total", rows=[{"value": 99441}])
    chart, reason = chart_for_result(data)
    assert chart is None and "single total" in reason
    assert "99,441 orders" in make_result_report("count", [data], True).answer


def test_category_month_chart_preserves_category_series():
    data = result(group="category_month", metric="category_sales", rows=[
        {"month": "2017-01-01", "category": "books", "value": "12.50"},
        {"month": "2017-01-01", "category": "toys", "value": "42.00"},
    ])
    chart, reason = chart_for_result(data)
    assert chart.chart_type == "line" and chart.series == "category"
    assert len(build_chart(chart, data).data) == 2
    assert reason is None


def test_large_category_result_is_not_silently_cut_to_top_n_for_a_graph():
    data = result(group="category", metric="category_sales", rows=[{"category": f"Category {i}", "value": i} for i in range(31)])
    chart, reason = chart_for_result(data)
    assert chart is None and "Too many categories" in reason
    report = make_result_report("categories", [data], True)
    assert report.tables == [data.result_id] and not report.charts

