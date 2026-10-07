"""Small, conservative constraints for explicit scopes the model must not lose.

This is not a general natural-language SQL parser. It recognizes clear calendar
year/month comparisons and chart instructions, then validates the complete
QuerySpec again. Ambiguous dates and unsupported dimensions remain model work.
"""
from dataclasses import dataclass
from datetime import date
import re
import unicodedata

from olist_agent.models import QuerySpec


_CHART = re.compile(r"\b(?:charts?|graphs?|plots?|pie|bars?|scatter|visuali[sz](?:e|ation|ations))\b", re.I)
_NO_CHART = re.compile(
    r"\b(?:do\s+not|don't|dont|no|without|never)\s+(?:(?:generate|show|create|draw|any|a|the)\s+)*"
    r"(?:charts?|graphs?|plots?|draw|visuali[sz](?:e|ation|ations))\b|\b(?:charts?|graphs?|plots?)\s+off\b",
    re.I,
)
_MONTH_NAMES = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december")
_STATES = {
    "AC": "acre", "AL": "alagoas", "AP": "amapa", "AM": "amazonas", "BA": "bahia",
    "CE": "ceara", "DF": "distrito federal", "ES": "espirito santo", "GO": "goias",
    "MA": "maranhao", "MT": "mato grosso", "MS": "mato grosso do sul", "MG": "minas gerais",
    "PA": "para", "PB": "paraiba", "PR": "parana", "PE": "pernambuco", "PI": "piaui",
    "RJ": "rio de janeiro", "RN": "rio grande do norte", "RS": "rio grande do sul",
    "RO": "rondonia", "RR": "roraima", "SC": "santa catarina", "SP": "sao paulo",
    "SE": "sergipe", "TO": "tocantins",
}
_STATUSES = ("created", "approved", "invoiced", "processing", "shipped", "delivered", "unavailable", "canceled")


@dataclass(frozen=True)
class IntentConstraints:
    metric: str | None = None
    group_by: str | None = None
    years: tuple[int, ...] = ()
    start: date | None = None
    end: date | None = None
    chart_requested: bool = False
    chart_forbidden: bool = False
    preferred_chart: str | None = None
    comparison: bool = False
    states: tuple[str, ...] | None = None
    statuses: tuple[str, ...] | None = None
    clear_scope: bool = False
    complex_request: bool = False
    ambiguous_filter: bool = False
    label: str = ""

    @property
    def has_query_scope(self):
        """A graph-only follow-up is intentionally not a new data request."""
        return bool(self.metric or self.years or self.start or self.end or self.group_by)

    @property
    def exact_plan(self):
        return bool(self.clear_scope and not self.ambiguous_filter and self.metric and (self.years or self.start)
                    and self.group_by in ("total", "year", "month"))

    @property
    def can_complete(self):
        """Simple scoped summaries can finish from results without an AI roundtrip."""
        return self.exact_plan and not self.complex_request


def parse_question(question: str) -> IntentConstraints:
    """Extract only explicit supported intent; never infer a sample state/year."""
    text = question.lower().replace("’", "'")
    normalized = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    forbidden = bool(_NO_CHART.search(text))
    chart = bool(_CHART.search(text)) and not forbidden
    preferred = next((kind for kind in ("pie", "scatter", "line", "bar") if re.search(rf"\b{kind}(?:s)?\b", text)), None)
    comparison = bool(re.search(r"\b(?:compar\w*|comapar\w*|versus|vs\.?|difference)\b", text))
    complex_request = bool(re.search(r"\b(?:why|caus\w*|reconcil\w*|contribut\w*|correlat\w*|forecast\w*|predict\w*)\b|\b(?:percent(?:age)?\s+change|growth\s+rate)\b", text))
    sql_evidence_request = bool(re.search(r"\b(?:show|display|see|view|explain)\s+(?:me\s+)?(?:the\s+)?(?:sql|query)\b|\bwhat\s+(?:sql|query)\s+(?:was\s+)?used\b", text))
    excluded_year = bool(re.search(r"\b(?:excluding|except|without|not|omit|skip)\s+(?:the\s+)?(?:year\s+)?(?:19|20|21)\d{2}\b", text))
    complex_request = complex_request or sql_evidence_request or excluded_year
    monthly = bool(re.search(r"\b(?:monthly|month[-\s]?wise|month\s*(?:by|to|over|-)\s*month|by\s+month|per\s+month|(?:each|every)\s+month)\b", text))
    annually = bool(re.search(r"\b(?:yearly|year[-\s]?wise|annually|annual|by\s+year|per\s+year|(?:each|every)\s+year)\b", text))
    category = bool(re.search(r"\bcategor(?:y|ies)\b", text))
    payment = bool(re.search(r"\bpayment\s+(?:methods?|types?|mix|shares?)\b", text))
    status_words = [s for s in _STATUSES if re.search(rf"\b{s}\b", text)]
    if re.search(r"\bcancelled\b", text) and "canceled" not in status_words:
        status_words.append("canceled")
    all_statuses = bool(re.search(r"\ball\s+(?:order\s+|recorded\s+)?statuses\b|\bregardless\s+of\s+(?:order\s+)?status\b|\bnot\s+(?:only|just)\s+delivered\b|\bincluding\s+delivered\b", text))
    negated_status = bool(re.search(r"\b(?:not|except|excluding|without)\s+(?:delivered|cancell?ed|canceled|shipped|approved|processing|created|invoiced|unavailable)\b", text))
    if all_statuses:
        status_words = []
    if comparison and len(status_words) > 1:
        complex_request = True

    metric = None
    if payment:
        if re.search(r"\b(?:value|amount)\b", text):
            metric = "payment_value_share"
        elif re.search(r"\borders?\b", text):
            metric = "payment_order_share"
        elif re.search(r"\brecords?\b", text):
            metric = "payment_record_share"
    elif category:
        if re.search(r"\b(?:sales|revenue|price|value)\b", text):
            metric = "category_sales"
    elif re.search(r"\b(?:late\s+delivery\s+rate|late\s+deliveries\s+(?:percentage|rate))\b", text):
        metric = "late_delivery_rate"
    elif re.search(r"\b(?:average|mean)\s+(?:delivery\s+(?:duration|time|days)|days\s+to\s+deliver)\b", text):
        metric = "delivery_days"
    elif re.search(r"\b(?:aov|average\s+order\s+(?:value|sales))\b", text):
        metric = "aov"
    elif re.search(r"\bfreight\b", text):
        metric = "freight"
    elif re.search(r"\b(?:recorded\s+payments?|payment\s+value|total\s+payments?)\b", text):
        metric = "recorded_payments"
    elif re.search(r"\bitem\s+(?:sales|prices?|value)\b", text):
        metric = "item_sales"
    elif re.search(r"\borders?\b", text):
        metric = "delivered_orders" if status_words == ["delivered"] and not negated_status else "placed_orders"

    # This planner represents exactly one metric. Never silently discard a
    # second requested measure just because it matched a higher-priority phrase.
    measure_patterns = (
        r"\b(?:orders|order\s+(?:count|volume))\b",
        r"\bitem\s+(?:sales|prices?|value)\b",
        r"\bfreight\b",
        r"\b(?:recorded\s+payments?|payment\s+value|total\s+payments?)\b",
        r"\b(?:aov|average\s+order\s+(?:value|sales))\b",
        r"\b(?:average|mean)\s+(?:delivery\s+(?:duration|time|days)|days\s+to\s+deliver)\b",
        r"\blate\s+delivery\s+rate\b",
    )
    multiple_metrics = sum(bool(re.search(pattern, text)) for pattern in measure_patterns) > 1 and bool(re.search(r"\b(?:and|versus|vs)\b|[&,]", text))
    if multiple_metrics:
        metric = None
        complex_request = True

    # Abbreviations are matched in the original text, avoiding accidental 'am',
    # 'to', etc. Full names are accent-insensitive and longest names win.
    states = set(re.findall(r"\b(?:" + "|".join(_STATES) + r")\b", question))
    remaining = normalized
    for code, name in sorted(_STATES.items(), key=lambda item: len(item[1]), reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", remaining):
            states.add(code)
            remaining = re.sub(rf"\b{re.escape(name)}\b", " ", remaining)
    # An unsupported city/customer/geographic constraint cannot become an
    # apparently correct whole-state/dataset query. Only known state names,
    # statuses and calendar words are safe to remove from explicit filter text.
    ambiguous_filter = negated_status or bool(re.search(r"\b(?:city|cities|zip\s*code|postcode|postal\s*code|customer_id|seller_id|country)\b", text))
    allowed_filter_words = {code.lower() for code in _STATES} | {name.split()[0] for name in _STATES.values()}
    allowed_filter_words.update(_STATUSES)
    allowed_filter_words.update(_MONTH_NAMES)
    allowed_filter_words.update(("cancelled", "all", "states", "state", "brazil", "year", "years",
                                 "each", "every", "both", "comparison", "orders", "order", "these", "those"))
    for match in re.finditer(r"\b(?:in|from|for)\s+(?:the\s+)?([a-z]+)\b", normalized):
        if match.group(1) not in allowed_filter_words:
            ambiguous_filter = True

    raw_years = sorted({int(y) for y in re.findall(r"\b(?:19|20|21)\d{2}\b", text)})
    # Avoid converting 'after 2016', 'December 2017' or a precise date into an
    # invented full-year interval. A model may still plan those explicit dates.
    precise_dates = bool(re.search(r"\b\d{4}-\d{1,2}(?:-\d{1,2})?\b", text))
    directional = bool(re.search(r"\b(?:after|before|since|until|through|starting|ending)\b", text))
    named_months = [i + 1 for i, month in enumerate(_MONTH_NAMES) if re.search(rf"\b{month}\b", text)]
    years, start, end = (), None, None
    if raw_years and not precise_dates and not directional and not named_months:
        explicit_range = bool(re.search(r"\b\d{4}\s*(?:to|[-–—])\s*\d{4}\b", text))
        if len(raw_years) == 2 and (explicit_range or (not comparison and re.search(r"\b(?:from|between)\b", text))):
            raw_years = list(range(raw_years[0], raw_years[-1] + 1))
        years = tuple(raw_years)
        start, end = date(years[0], 1, 1), date(years[-1] + 1, 1, 1)
    elif len(raw_years) == 1 and len(named_months) == 1 and not precise_dates and not directional:
        year, month = raw_years[0], named_months[0]
        start = date(year, month, 1)
        end = date(year + (month == 12), 1 if month == 12 else month + 1, 1)

    grouping = None
    if payment:
        grouping = "payment_method"
    elif category:
        grouping = "category_month" if monthly else "category"
    elif re.search(r"\b(?:by|per|each)\s+(?:customer\s+)?state\b|\bstates?\s+(?:comparison|breakdown)\b", text):
        grouping = "state"
    elif monthly:
        grouping = "month"
    elif annually or (len(years) > 1 and comparison):
        grouping = "year"
    elif metric and (years or start or re.search(r"\b(?:how\s+many|total|count|number\s+of)\b", text)):
        grouping = "total"

    clear = bool(metric and (years or start or grouping in ("month", "year", "total")))
    # Explicit intrinsic delivered-order semantics do not require an additional
    # redundant status filter. Other requested statuses must remain exact.
    statuses = () if metric == "delivered_orders" else tuple(status_words)
    if all_statuses:
        statuses = ()
    if not clear and not status_words:
        statuses = None
    if negated_status:
        statuses = None
    requested_states = tuple(sorted(states)) if states or clear else None
    parts = [metric.replace("_", " ")] if metric else []
    if grouping:
        parts.append("by " + grouping.replace("_", " "))
    if years:
        parts.append("for " + ", ".join(str(y) for y in years))
    elif start:
        parts.append(f"from {start.isoformat()} to {end.isoformat()} (end exclusive)")
    if sql_evidence_request or excluded_year:
        parts = []
    return IntentConstraints(metric=metric, group_by=grouping, years=years, start=start, end=end,
        chart_requested=chart, chart_forbidden=forbidden, preferred_chart=preferred,
        comparison=comparison, states=requested_states, statuses=statuses,
        clear_scope=clear, complex_request=complex_request,
        ambiguous_filter=ambiguous_filter, label=" ".join(parts))


def constrain_query(spec: QuerySpec, constraints: IntentConstraints) -> QuerySpec:
    """Keep the query aligned with clear user scope before SQL approval.

    Returning a newly validated object prevents model_copy's validation bypass.
    No SQL execution or database access occurs here.
    """
    data = spec.model_dump()
    for name in ("metric", "group_by", "start", "end"):
        value = getattr(constraints, name)
        if value is not None:
            data[name] = value
    if constraints.years:
        data["years"] = list(constraints.years)
    if constraints.states is not None:
        data["states"] = list(constraints.states)
    if constraints.statuses is not None:
        data["statuses"] = list(constraints.statuses)
    return QuerySpec.model_validate(data)
