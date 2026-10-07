"""Compare actual imported PostgreSQL records with independent source calculations."""
from decimal import Decimal
import json
import os
from pathlib import Path
from uuid import uuid4
import pandas as pd
from sqlalchemy import text
from olist_agent.config import Settings
from olist_agent.db.connection import engine
from olist_agent.db.schema import SPECS
from olist_agent.models import QuerySpec
from olist_agent.services.store import Store
from olist_agent.analytics.query import QueryService


def main():
    root=Path(__file__).resolve().parents[1]
    settings=Settings.load()
    admin=engine(os.environ["ADMIN_DATABASE_URL"])
    store=Store(engine(settings.app_url))
    service=QueryService(engine(settings.analytics_url),settings,store)
    session=str(uuid4())
    checks={}
    for name,(filename,_,_) in SPECS.items():
        expected=sum(len(chunk) for chunk in pd.read_csv(root/"data"/"raw"/filename,dtype=str,chunksize=50000))
        with admin.connect() as conn:
            actual=conn.execute(text(f"SELECT COUNT(*) FROM raw.{name}")).scalar_one()
        assert actual==expected, f"{name}: source {expected}, database {actual}"
        checks[name]={"source_rows":expected,"database_rows":actual}
        print(f"Verified {name}: {actual:,} rows",flush=True)
    orders=pd.read_csv(root/"data"/"raw"/SPECS["orders"][0],dtype=str)
    items=pd.read_csv(root/"data"/"raw"/SPECS["order_items"][0],dtype=str)
    payments=pd.read_csv(root/"data"/"raw"/SPECS["order_payments"][0],dtype=str)
    expected={"placed_orders":Decimal(orders.order_id.nunique()),
              "delivered_orders":Decimal(int(orders.order_status.eq("delivered").sum())),
              "item_sales":sum(map(Decimal,items.price.dropna()),Decimal(0)),
              "freight":sum(map(Decimal,items.freight_value.dropna()),Decimal(0)),
              "recorded_payments":sum(map(Decimal,payments.payment_value.dropna()),Decimal(0))}
    for metric,value in expected.items():
        result=service.execute(QuerySpec(metric=metric),session)
        actual=Decimal(str(result.rows[0]["value"]))
        assert actual==value, f"{metric}: source {value}, database {actual}"
        checks[metric]={"independent_source_value":str(value),"database_value":str(actual),"result_id":result.result_id}
        print(f"Verified {metric}: {actual}",flush=True)
    monthly=service.execute(QuerySpec(metric="placed_orders",group_by="month",start="2017-01-01",end="2018-01-01"),session)
    dates=pd.to_datetime(orders.order_purchase_timestamp)
    filtered=orders.loc[dates.ge("2017-01-01") & dates.lt("2018-01-01")].copy()
    filtered["month"]=pd.to_datetime(filtered.order_purchase_timestamp).dt.strftime("%Y-%m-01")
    source_months={month:int(count) for month,count in filtered.groupby("month").order_id.nunique().items()}
    db_months={row["month"]:int(row["value"]) for row in monthly.rows}
    assert source_months==db_months
    checks["monthly_orders_2017"]={"source":source_months,"database":db_months}
    categories=service.execute(QuerySpec(metric="category_sales",group_by="category"),session)
    assert not categories.truncated
    assert sum(Decimal(row["value"]) for row in categories.rows)==expected["item_sales"]
    checks["category_reconciliation"]="Category prices reconcile exactly to source item sales; no payment attribution."
    # Repeat indexing and fixture tests are separate; no LLM is involved here.
    checks["coverage"]=service.coverage()
    checks["query_access"]="Real PostgreSQL analytics role and app history/RLS used successfully."
    target=root/".runtime"/"import-verification.json"
    target.write_text(json.dumps(checks,indent=2),encoding="utf-8")
    print(f"Source/database verification passed. Report: {target}")


if __name__=="__main__":
    main()
