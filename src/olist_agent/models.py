from datetime import date
from typing import Annotated, Any, Literal
from uuid import uuid4
from pydantic import BaseModel, Field, model_validator, ConfigDict

MetricID = Literal["placed_orders", "delivered_orders", "item_sales", "freight", "recorded_payments",
                   "aov", "delivery_days", "late_delivery_rate", "category_sales",
                   "payment_record_share", "payment_value_share", "payment_order_share"]


class QuerySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: MetricID
    group_by: Literal["total", "year", "month", "state", "category", "category_month", "payment_method"] = "total"
    start: date | None = None
    end: date | None = None
    statuses: list[Literal["created", "approved", "invoiced", "processing", "shipped", "delivered", "unavailable", "canceled"]] = Field(default_factory=list, max_length=8)
    states: list[str] = Field(default_factory=list, max_length=27)
    years: list[Annotated[int, Field(ge=1900, le=2100)]] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_scope(self):
        if self.start and self.end and self.start >= self.end:
            raise ValueError("end must follow start (end is exclusive)")
        if self.metric == "category_sales" and self.group_by not in ("total", "category", "category_month", "month", "state"):
            raise ValueError("Invalid category sales grouping")
        if self.metric.startswith("payment_") and self.group_by != "payment_method":
            raise ValueError("Payment shares require payment_method grouping")
        if not self.metric.startswith("payment_") and self.group_by == "payment_method":
            raise ValueError("Payment grouping requires an explicit payment-share metric")
        if self.metric != "category_sales" and self.group_by in ("category", "category_month"):
            raise ValueError("Category analysis requires category_sales; order-level values cannot be assigned to categories")
        if any(len(s) != 2 or not s.isalpha() or s != s.upper() for s in self.states):
            raise ValueError("States must be two uppercase letters")
        self.years = sorted(set(self.years))
        return self


class SQLQuerySpec(BaseModel):
    """Model-authored SQL; other fields describe presentation, never build SQL."""
    model_config = ConfigDict(extra="forbid")
    sql: str = Field(min_length=1, max_length=20000)
    title: str = Field(default="Query result", min_length=1, max_length=160)
    metric: MetricID | Literal["custom"] = "custom"
    units: Literal["BRL", "%", "orders", "days", "value"] = "value"
    x: str | None = None
    y: str = "value"
    series: str | None = None
    @property
    def group_by(self):
        return "category_month" if self.x == "month" and self.series == "category" else self.x or "total"

    @property
    def years(self):
        return []

    @property
    def start(self):
        return None

    @property
    def end(self):
        return None


class QueryResult(BaseModel):
    result_id: str = Field(default_factory=lambda: str(uuid4()))
    columns: list[str]
    types: dict[str, str]
    rows: list[dict[str, Any]]
    metric_id: str
    spec: SQLQuerySpec | QuerySpec
    query: str
    parameters: dict[str, Any]
    truncated: bool = False
    warnings: list[str] = Field(default_factory=list)
    provenance: str


class ChartSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    result_id: str
    chart_type: Literal["bar", "grouped_bar", "line", "pie", "scatter"]
    x: str
    y: str = "value"
    series: str | None = None
    title: str = Field(min_length=1, max_length=200)
    units: Literal["BRL", "%", "orders", "days", "value"] = "value"


class Finding(BaseModel):
    text: str
    result_ids: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)


class Report(BaseModel):
    answer: str
    findings: list[Finding] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    charts: list[ChartSpec] = Field(default_factory=list)
    metric_ids: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
