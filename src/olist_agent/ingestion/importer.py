from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
import json
import pandas as pd
from pydantic import BaseModel, Field
from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from olist_agent.db.schema import SPECS, tables, imports


class FileReport(BaseModel):
    source: str
    columns: list[str] = Field(default_factory=list)
    imported: int = 0
    rejected: int = 0
    duplicates: int = 0
    repeated_source_rows: int = 0
    missing: dict[str, int] = Field(default_factory=dict)
    dates: dict[str, list[str]] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


class ImportReport(BaseModel):
    source_kind: str
    files: dict[str, FileReport]
    timezone: str = "Unconfirmed source timezone; timestamps preserved without timezone conversion."


def convert(value, kind):
    if value is None or pd.isna(value) or value == "":
        return None
    value = str(value)
    if kind == "date":
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        return parsed
    if kind in ("money", "coordinate", "int"):
        number = Decimal(value)
        if not number.is_finite():
            raise ValueError("Non-finite numeric value")
        if kind == "int":
            if number != number.to_integral_value() or number < 0:
                raise ValueError("Expected nonnegative integer")
            return int(number)
        scale = Decimal("0.01") if kind == "money" else Decimal("0.0000000001")
        if kind == "money" and number < 0:
            raise ValueError("Negative monetary value")
        if kind == "money" and number != number.quantize(scale):
            raise ValueError("Numeric precision exceeds source contract")
        return number
    return value


def inspect_files(folder):
    folder = Path(folder)
    found = {}
    for name, (filename, _, fields) in SPECS.items():
        path = folder / filename
        if not path.is_file():
            raise ValueError(f"Missing {path}. Download Olist CSVs or explicitly use tests/fixtures/raw.")
        columns = pd.read_csv(path, nrows=0).columns.tolist()
        missing = set(fields) - set(columns)
        extra = set(columns) - set(fields)
        if missing or extra:
            raise ValueError(f"{filename}: missing columns {sorted(missing)}, unexpected columns {sorted(extra)}. Inspect contract before importing.")
        found[name] = columns
    return found


def import_csvs(db, folder, source_kind="olist", batch_size=2000, progress=None):
    if source_kind not in ("olist", "fixture"):
        raise ValueError("source_kind must be olist or fixture")
    headers = inspect_files(folder)  # Validate all actual headers before any writes.
    report = ImportReport(source_kind=source_kind, files={})
    with db.begin() as conn:
        existing_kinds = {r["source_kind"] for r in conn.execute(select(imports.c.report)).scalars()}
        if existing_kinds and existing_kinds != {source_kind}:
            raise ValueError("Refusing to mix fixture and Olist data. Use a separate PostgreSQL database.")
        for name, (filename, keys, fields) in SPECS.items():
            table = tables[name]
            stats = FileReport(source=filename, columns=headers[name], missing={f: 0 for f in fields})
            report.files[name] = stats
            statement = pg_insert(table).on_conflict_do_nothing().returning(*table.primary_key)
            occurrences = Counter()
            for frame in pd.read_csv(Path(folder) / filename, dtype=str, keep_default_na=False, chunksize=batch_size):
                batch = []
                for source_row in frame.to_dict("records"):
                    for f in fields:
                        if source_row[f] == "":
                            stats.missing[f] += 1
                    fingerprint = sha256(json.dumps(source_row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                    occurrences[fingerprint] += 1
                    if occurrences[fingerprint] > 1:
                        stats.repeated_source_rows += 1
                    try:
                        row = {f: convert(source_row[f], k) for f, k in fields.items()}
                        if keys == "source_key":
                            row["source_key"] = f"{fingerprint}:{occurrences[fingerprint]}"
                        for column in table.columns:
                            if not column.nullable and row.get(column.name) is None:
                                raise ValueError(f"Required {column.name} is missing")
                        for f, kind in fields.items():
                            if kind == "date" and row[f] is not None:
                                value = row[f].isoformat()
                                bounds = stats.dates.setdefault(f, [value, value])
                                bounds[0], bounds[1] = min(bounds[0], value), max(bounds[1], value)
                        batch.append(row)
                    except (ValueError, InvalidOperation) as exc:
                        stats.rejected += 1
                        if len(stats.errors) < 10:
                            stats.errors.append(str(exc))
                if batch:
                    # Bulk insert; fall back to isolated rows if any constraint fails.
                    try:
                        with conn.begin_nested():
                            result = conn.execute(statement, batch)
                            count = len(result.fetchall())
                        stats.imported += count
                        stats.duplicates += len(batch) - count
                    except IntegrityError:
                        for row in batch:
                            try:
                                with conn.begin_nested():
                                    result = conn.execute(statement, row)
                                    exists = result.first() is not None
                                stats.imported += int(exists)
                                stats.duplicates += int(not exists)
                            except IntegrityError:
                                stats.rejected += 1
                                if len(stats.errors) < 10:
                                    stats.errors.append("Database constraint violation; row rejected")
                if progress:
                    progress(name, stats, False)
            if progress:
                progress(name, stats, True)
        conn.execute(insert(imports).values(id=str(uuid4()), created_at=datetime.now(), report=report.model_dump(mode="json")))
    return report
