CREATE OR REPLACE VIEW analytics.order_facts AS
WITH i AS (
 SELECT order_id, SUM(price) AS item_sales_value, SUM(freight_value) AS freight_value,
 COUNT(*) AS item_count, COUNT(*) FILTER (WHERE price IS NULL) AS missing_item_prices,
 COUNT(*) FILTER (WHERE freight_value IS NULL) AS missing_freight_values
 FROM raw.order_items GROUP BY order_id
), p AS (
 SELECT order_id, SUM(payment_value) AS recorded_payment_value,
 COUNT(*) AS payment_count, COUNT(*) FILTER (WHERE payment_value IS NULL) AS missing_payment_values
 FROM raw.order_payments GROUP BY order_id
)
SELECT o.*, c.customer_unique_id, c.customer_state,
 i.item_sales_value, i.freight_value, COALESCE(i.item_count,0) AS item_count,
 COALESCE(i.missing_item_prices,0) AS missing_item_prices,
 COALESCE(i.missing_freight_values,0) AS missing_freight_values,
 p.recorded_payment_value, COALESCE(p.payment_count,0) AS payment_count,
 COALESCE(p.missing_payment_values,0) AS missing_payment_values,
 CASE WHEN o.order_status='delivered' AND o.order_delivered_customer_date >= o.order_purchase_timestamp
 THEN EXTRACT(EPOCH FROM (o.order_delivered_customer_date-o.order_purchase_timestamp))/86400 END AS delivery_days,
 CASE WHEN o.order_status='delivered' AND o.order_delivered_customer_date IS NOT NULL
 AND o.order_estimated_delivery_date IS NOT NULL
 THEN o.order_delivered_customer_date > o.order_estimated_delivery_date END AS is_late
FROM raw.orders o LEFT JOIN raw.customers c USING(customer_id)
LEFT JOIN i USING(order_id) LEFT JOIN p USING(order_id);

CREATE OR REPLACE VIEW analytics.category_items AS
SELECT i.order_id, i.order_item_id, i.price, i.freight_value,
 o.order_purchase_timestamp, o.order_status, c.customer_state,
 COALESCE(t.product_category_name_english,p.product_category_name,'Unknown') AS category
FROM raw.order_items i JOIN raw.orders o USING(order_id)
LEFT JOIN raw.products p USING(product_id)
LEFT JOIN raw.category_translation t USING(product_category_name)
LEFT JOIN raw.customers c USING(customer_id);

CREATE OR REPLACE VIEW analytics.payment_records AS
SELECT p.*, o.order_purchase_timestamp, o.order_status, c.customer_state
FROM raw.order_payments p JOIN raw.orders o USING(order_id)
LEFT JOIN raw.customers c USING(customer_id);
