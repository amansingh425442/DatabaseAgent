from pydantic import BaseModel


class MetricDefinition(BaseModel):
    id: str
    title: str
    grain: str
    formula: str
    statuses: str = "All recorded statuses by default; explicit status filters are reported."
    date_field: str = "order_purchase_timestamp; half-open [start, end) intervals"
    currency: str = "BRL for monetary metrics; source timestamps have unconfirmed timezone"
    null_handling: str
    denominator: str
    attribution: str
    source: str = "Project metric contract v1; expected Olist CSV column names, verified at import"


def metric(id, title, grain, formula, nulls, denominator="None", attribution="Order purchase date and customer_state", **kwargs):
    return MetricDefinition(id=id, title=title, grain=grain, formula=formula,
                            null_handling=nulls, denominator=denominator, attribution=attribution, **kwargs)


METRICS = {m.id: m for m in [
    metric("placed_orders", "Placed order count", "One row per order", "COUNT(DISTINCT order_id)", "Purchase timestamp is required at import"),
    metric("delivered_orders", "Delivered order count", "One row per order", "COUNT(DISTINCT order_id) FILTER delivered", "Delivery timestamp not required for status count", statuses="Only delivered, intersected with any explicit status filter"),
    metric("item_sales", "Item sales value", "Item price, summed once per order", "SUM(item_sales_value)", "Missing prices excluded; missing counts warned; empty sum is zero"),
    metric("freight", "Freight value", "Item freight, summed once per order", "SUM(freight_value)", "Missing freight excluded and warned; empty sum is zero"),
    metric("recorded_payments", "Recorded payment value", "Payment record, summed once per order", "SUM(recorded_payment_value)", "Missing values excluded and warned; empty sum is zero"),
    metric("aov", "Average item sales per placed order", "One row per placed order", "SUM(item_sales_value) / COUNT(DISTINCT order_id)", "Orders without priced items contribute zero to numerator and remain in denominator; missing prices warned; empty denominator yields null", "All placed orders under matching filters; excludes freight"),
    metric("delivery_days", "Mean delivery duration", "One eligible delivered order", "AVG(delivery_days)", "Exclude missing delivery dates or delivery before purchase; empty denominator yields null", "Delivered orders with nonnegative known purchase-to-delivery duration", statuses="Delivered only"),
    metric("late_delivery_rate", "Late delivery rate (%)", "One eligible delivered order", "100 * late orders / eligible orders", "Exclude missing delivery or estimated timestamps; empty denominator yields null", "Delivered orders with both delivered and estimated timestamps", statuses="Delivered only"),
    metric("category_sales", "Category item sales", "One order item", "SUM(price)", "Missing category labelled Unknown; untranslated categories retain Portuguese name; null prices excluded and warned", attribution="Item category and order purchase date; never attribute whole-order payments to categories"),
    metric("payment_record_share", "Payment record share (%)", "One payment record", "100 * records for method / all matching payment records", "Unknown method labelled Unknown", "All payment records matching period, status, state filters", attribution="Payment method; multiple records per order remain distinct"),
    metric("payment_value_share", "Payment value share (%)", "One payment record", "100 * value for method / total matching recorded payment value", "Null payment values excluded and warned; zero total yields null", "Recorded payment value under matching filters", attribution="Payment method; not item sales"),
    metric("payment_order_share", "Orders using payment method (%)", "Distinct order-method pair", "100 * distinct orders for method / all matching orders with payment records", "Unknown method labelled Unknown; zero denominator yields null", "Distinct orders with at least one payment record; shares can exceed 100% combined", attribution="An order can use several methods; unsuitable for a pie chart"),
]}


def get_definition(metric_id):
    if metric_id not in METRICS:
        raise ValueError("Unknown metric ID")
    return METRICS[metric_id]
