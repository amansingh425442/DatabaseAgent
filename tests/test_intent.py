"""Business-scope regressions for deterministic constraints, not an NLP benchmark."""
from datetime import date

import pytest

from olist_agent.agent.intent import parse_question


def test_screenshot_question_keeps_only_requested_years_and_no_sample_state():
    intent = parse_question("comapare orders of 2016 and 2018 year with help of graph and charts")
    assert intent.metric == "placed_orders"
    assert intent.group_by == "year"
    assert intent.years == (2016, 2018)
    assert intent.start == date(2016, 1, 1)
    assert intent.end == date(2019, 1, 1)
    assert intent.chart_requested and intent.exact_plan and intent.can_complete
    assert intent.states == intent.statuses == ()


@pytest.mark.parametrize("question,group,years", [
    ("Compare 2016 and 2018 orders", "year", (2016, 2018)),
    ("Compare monthly orders for 2016 and 2018", "month", (2016, 2018)),
    ("Monthly orders 2017", "month", (2017,)),
    ("Orders by year from 2016 to 2018", "year", (2016, 2017, 2018)),
    ("How many orders between 2016 and 2018?", "total", (2016, 2017, 2018)),
    ("How many delivered orders in 2018?", "total", (2018,)),
])
def test_clear_calendar_scope(question, group, years):
    intent = parse_question(question)
    assert intent.group_by == group
    assert intent.years == years
    assert intent.can_complete


@pytest.mark.parametrize("question", [
    "Orders after 2016", "Orders before 2018", "Orders since 2017", "Orders until 2018",
    "Orders between 2016-09-01 and 2018-03-01", "Compare December 2016 and December 2018 orders",
])
def test_ambiguous_or_precise_period_is_not_expanded_to_full_calendar_years(question):
    intent = parse_question(question)
    assert not intent.years
    assert not intent.can_complete


def test_single_named_month_is_a_precise_half_open_interval():
    intent = parse_question("How many orders in December 2017?")
    assert intent.start == date(2017, 12, 1)
    assert intent.end == date(2018, 1, 1)
    assert intent.years == ()
    assert intent.can_complete


@pytest.mark.parametrize("question", ["draw graphs", "show charts", "plot it", "visualize this", "make a bar chart"])
def test_chart_only_followup_reuses_evidence(question):
    intent = parse_question(question)
    assert intent.chart_requested
    assert not intent.has_query_scope
    assert not intent.can_complete


@pytest.mark.parametrize("question", [
    "compare orders 2016 and 2018, no charts", "do not generate any charts",
    "without graphs", "don't draw a graph", "graphs off", "no plot please",
])
def test_explicit_chart_denial_wins(question):
    intent = parse_question(question)
    assert intent.chart_forbidden and not intent.chart_requested


@pytest.mark.parametrize("question,metric,states,statuses", [
    ("Compare delivered orders 2016 and 2018 in SP", "delivered_orders", ("SP",), ()),
    ("Compare cancelled orders 2016 and 2018 from RJ", "placed_orders", ("RJ",), ("canceled",)),
    ("Monthly orders 2017 in São Paulo", "placed_orders", ("SP",), ()),
    ("Orders by year 2016 and 2018 in Mato Grosso do Sul", "placed_orders", ("MS",), ()),
    ("Orders by year 2016 and 2018 in SP and RJ", "placed_orders", ("RJ", "SP"), ()),
])
def test_explicit_supported_filters_are_recognized(question, metric, states, statuses):
    intent = parse_question(question)
    assert intent.metric == metric
    assert intent.states == states and intent.statuses == statuses
    assert intent.can_complete


@pytest.mark.parametrize("question", [
    "How many orders in Chicago 2017?", "Compare orders for Alice 2016 and 2018",
    "How many orders in city São Paulo 2017?", "How many orders for customer_id abc in 2017?",
])
def test_unparsed_filters_do_not_get_a_whole_dataset_fast_plan(question):
    intent = parse_question(question)
    assert intent.ambiguous_filter
    assert not intent.exact_plan and not intent.can_complete


@pytest.mark.parametrize("question", [
    "Why did orders increase between 2016 and 2018?", "Reconcile monthly orders 2017",
    "Percentage change of orders 2016 versus 2018", "Predict orders for 2018",
])
def test_complex_questions_are_not_reduced_to_plain_count_completion(question):
    intent = parse_question(question)
    assert not intent.can_complete


def test_category_and_payment_requests_keep_their_grain():
    category = parse_question("Compare monthly category sales 2016 and 2018")
    assert category.metric == "category_sales" and category.group_by == "category_month"
    assert not category.can_complete
    payment = parse_question("Payment method value shares 2018")
    assert payment.metric == "payment_value_share" and payment.group_by == "payment_method"
    assert not payment.can_complete


def test_unknown_business_metric_is_left_for_the_model():
    intent = parse_question("Compare customer satisfaction 2016 and 2018")
    assert intent.metric is None and not intent.can_complete


def test_mentioned_delivered_status_does_not_override_explicit_all_statuses():
    intent = parse_question("Compare orders 2016 and 2018, all statuses including delivered")
    assert intent.metric == "placed_orders" and intent.statuses == ()
    assert intent.can_complete


def test_negative_status_filter_requires_a_full_model_plan():
    intent = parse_question("How many orders not delivered in 2018?")
    assert intent.ambiguous_filter and not intent.exact_plan
    assert intent.statuses is None and intent.metric == "placed_orders"


def test_multi_metric_request_does_not_collapse_to_a_single_fast_query():
    intent = parse_question("Compare orders and item sales 2016 and 2018")
    assert intent.complex_request and not intent.can_complete
    assert intent.metric is None


def test_excluded_year_does_not_get_an_inclusive_fast_plan():
    intent = parse_question("Compare orders 2016 and 2018 excluding 2016")
    assert intent.complex_request and not intent.can_complete
    assert intent.label == ""


def test_show_sql_request_does_not_trigger_a_new_data_query_plan():
    intent = parse_question("Show SQL used for orders 2016 and 2018")
    assert intent.complex_request and not intent.can_complete
    assert intent.label == ""


@pytest.mark.parametrize("question", [
    "Compare orders from 2016 to 2018", "Compare orders 2016-2018",
])
def test_explicit_year_range_includes_intermediate_year_even_in_comparison(question):
    intent = parse_question(question)
    assert intent.years == (2016, 2017, 2018)
    assert intent.group_by == "year" and intent.can_complete
