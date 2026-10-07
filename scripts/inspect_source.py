"""Profile downloaded CSVs before database ingestion; no production writes."""
import json
from pathlib import Path
import pandas as pd
from olist_agent.db.schema import SPECS
from olist_agent.ingestion.importer import inspect_files


def main():
    root=Path(__file__).resolve().parents[1]
    folder=root/"data"/"raw"
    headers=inspect_files(folder)
    report={}
    for name,(filename,keys,fields) in SPECS.items():
        frame=pd.read_csv(folder/filename,dtype=str,keep_default_na=False)
        item={"file":filename,"rows":len(frame),"columns":headers[name],
              "missing":{column:int(frame[column].eq("").sum()) for column in fields},
              "identical_rows":int(frame.duplicated().sum()),"dates":{},"invalid_values":{}}
        if keys != "source_key":
            item["duplicate_natural_keys"]=int(frame.duplicated(subset=[keys] if isinstance(keys,str) else list(keys)).sum())
        else:
            id_column="review_id" if name=="order_reviews" else "geolocation_zip_code_prefix"
            item["repeated_nonunique_id"]=int(frame.duplicated(subset=[id_column]).sum())
        for column,kind in fields.items():
            values=frame.loc[frame[column].ne(""),column]
            if kind=="date":
                parsed=pd.to_datetime(values,format="%Y-%m-%d %H:%M:%S",errors="coerce")
                item["invalid_values"][column]=int(parsed.isna().sum())
                item["dates"][column]=[str(parsed.min()),str(parsed.max())]
            elif kind in ("money","int","coordinate"):
                parsed=pd.to_numeric(values,errors="coerce")
                invalid=parsed.isna()
                if kind in ("money","int"):
                    invalid=invalid | parsed.lt(0)
                if kind=="int":
                    invalid=invalid | parsed.mod(1).ne(0)
                if column=="geolocation_lat":
                    invalid=invalid | ~parsed.between(-90,90)
                if column=="geolocation_lng":
                    invalid=invalid | ~parsed.between(-180,180)
                item["invalid_values"][column]=int(invalid.sum())
        if name=="orders":
            item["status_counts"]=frame.order_status.value_counts().to_dict()
        report[name]=item
        print(f"{name}: {len(frame):,} source rows, {item['identical_rows']:,} identical repeats, invalid numeric/date counts {sum(item['invalid_values'].values())}",flush=True)
    (root/".runtime").mkdir(exist_ok=True)
    (root/".runtime"/"source-inspection.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print("Detailed source profile saved to .runtime/source-inspection.json")


if __name__=="__main__":
    main()
