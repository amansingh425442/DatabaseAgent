"""Independent Pandas calculations check hand-authored fixture evaluation answers.

This validates the oracle; real PostgreSQL view agreement is tested separately.
"""
from decimal import Decimal
from pathlib import Path
import json
import pandas as pd

RAW=Path(__file__).parent/"fixtures"/"raw"


def test_fixture_hand_calculations_and_many_to_many_hazard():
    items=pd.read_csv(RAW/"olist_order_items_dataset.csv",dtype=str)
    payments=pd.read_csv(RAW/"olist_order_payments_dataset.csv",dtype=str)
    orders=pd.read_csv(RAW/"olist_orders_dataset.csv",dtype=str)
    assert len(orders)==4
    assert orders["order_status"].eq("delivered").sum()==3
    assert sum(map(Decimal,items["price"]))==250
    assert sum(map(Decimal,items["freight_value"]))==25
    assert sum(map(Decimal,payments["payment_value"]))==275
    naive=items.merge(payments,on="order_id")
    assert sum(map(Decimal,naive["price"]))==400  # Demonstrates multiplication.
    assert sum(map(Decimal,naive["payment_value"]))==440


def test_fixture_evaluation_months_categories_and_payment_records():
    cases=json.loads((Path(__file__).parent/"evaluations.json").read_text())
    orders=pd.read_csv(RAW/"olist_orders_dataset.csv",dtype=str)
    items=pd.read_csv(RAW/"olist_order_items_dataset.csv",dtype=str)
    products=pd.read_csv(RAW/"olist_products_dataset.csv",dtype=str,keep_default_na=False)
    translations=pd.read_csv(RAW/"product_category_name_translation.csv",dtype=str)
    payments=pd.read_csv(RAW/"olist_order_payments_dataset.csv",dtype=str)
    orders["month"]=pd.to_datetime(orders["order_purchase_timestamp"]).dt.strftime("%Y-%m-01")
    monthly=orders.groupby("month").order_id.nunique().to_dict()
    assert {k:str(v) for k,v in monthly.items()}==cases[0]["expected"]
    joined=items.merge(orders,on="order_id").merge(products,on="product_id").merge(translations,on="product_category_name",how="left")
    joined["category"]=joined["product_category_name_english"].fillna("Unknown")
    joined["price"]=joined["price"].map(Decimal)
    category=joined.groupby("category").price.sum().to_dict()
    assert category=={k:Decimal(v) for k,v in cases[1]["expected"].items()}
    comparison=joined.groupby(["month","category"]).price.sum().to_dict()
    assert {"/".join(k):v for k,v in comparison.items()}=={k:Decimal(v) for k,v in cases[2]["expected"].items()}
    shares={k:Decimal(int(v))*100/len(payments) for k,v in payments.groupby("payment_type").size().to_dict().items()}
    assert shares=={k:Decimal(v) for k,v in cases[3]["expected"].items()}
