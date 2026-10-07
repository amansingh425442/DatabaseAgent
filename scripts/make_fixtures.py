"""Synthetic test data, deliberately unlike production Olist population totals."""
from pathlib import Path
import csv
from olist_agent.db.schema import SPECS

ROOT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "raw"
ROOT.mkdir(parents=True, exist_ok=True)
data = {
    "customers": [dict(customer_id="c1", customer_unique_id="u1", customer_zip_code_prefix="00123", customer_city="Sao Paulo", customer_state="SP"),
                  dict(customer_id="c2", customer_unique_id="u1", customer_zip_code_prefix="00234", customer_city="Rio", customer_state="RJ")],
    "sellers": [dict(seller_id="s1", seller_zip_code_prefix="00123", seller_city="Sao Paulo", seller_state="SP")],
    "category_translation": [dict(product_category_name="livros", product_category_name_english="books")],
    "products": [dict(product_id="p1", product_category_name="livros", product_weight_g="100"),
                 dict(product_id="p2", product_weight_g="200")],
    "orders": [dict(order_id="o1", customer_id="c1", order_status="delivered", order_purchase_timestamp="2017-08-01 10:00:00", order_delivered_customer_date="2017-08-11 10:00:00", order_estimated_delivery_date="2017-08-10 10:00:00"),
               dict(order_id="o2", customer_id="c2", order_status="delivered", order_purchase_timestamp="2017-08-31 10:00:00", order_delivered_customer_date="2017-09-05 10:00:00", order_estimated_delivery_date="2017-09-06 10:00:00"),
               dict(order_id="o3", customer_id="c1", order_status="canceled", order_purchase_timestamp="2017-09-01 00:00:00"),
               dict(order_id="o4", customer_id="c2", order_status="delivered", order_purchase_timestamp="2017-09-30 10:00:00")],
    "order_items": [dict(order_id="o1", order_item_id="1", product_id="p1", seller_id="s1", price="100.00", freight_value="10.00"),
                    dict(order_id="o1", order_item_id="2", product_id="p2", seller_id="s1", price="50.00", freight_value="5.00"),
                    dict(order_id="o2", order_item_id="1", product_id="p2", seller_id="s1", price="80.00", freight_value="8.00"),
                    dict(order_id="o3", order_item_id="1", product_id="p1", seller_id="s1", price="20.00", freight_value="2.00")],
    "order_payments": [dict(order_id="o1", payment_sequential="1", payment_type="credit_card", payment_installments="1", payment_value="100.00"),
                       dict(order_id="o1", payment_sequential="2", payment_type="voucher", payment_installments="1", payment_value="65.00"),
                       dict(order_id="o2", payment_sequential="1", payment_type="credit_card", payment_installments="1", payment_value="88.00"),
                       dict(order_id="o3", payment_sequential="1", payment_type="debit_card", payment_installments="1", payment_value="22.00")],
    "order_reviews": [dict(review_id="r1", order_id="o1", review_score="5", review_creation_date="2017-08-12 00:00:00"),
                      dict(review_id="r1", order_id="o2", review_score="4", review_creation_date="2017-09-06 00:00:00"),
                      dict(review_id="r1", order_id="o2", review_score="4", review_creation_date="2017-09-06 00:00:00")],
    "geolocation": [dict(geolocation_zip_code_prefix="00123", geolocation_lat="-23.5", geolocation_lng="-46.6", geolocation_city="Sao Paulo", geolocation_state="SP"),
                    dict(geolocation_zip_code_prefix="00123", geolocation_lat="-23.5", geolocation_lng="-46.6", geolocation_city="Sao Paulo", geolocation_state="SP")],
}
for name, (filename, _, fields) in SPECS.items():
    with (ROOT / filename).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields))
        writer.writeheader()
        writer.writerows(data[name])
print("Wrote nine SYNTHETIC fixture CSVs to tests/fixtures/raw; these are not downloaded Olist data.")
