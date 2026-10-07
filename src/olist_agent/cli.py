import argparse
import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from .config import Settings
from .db.connection import engine


def main():
    parser = argparse.ArgumentParser(description="Olist database, ingestion and documentation commands")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    importer = sub.add_parser("import")
    importer.add_argument("folder", type=Path)
    importer.add_argument("--fixture", action="store_true", help="Explicitly label small test data")
    sub.add_parser("index")
    sub.add_parser("coverage")
    args = parser.parse_args()
    load_dotenv()
    settings = Settings.load()
    if args.command == "init-db":
        from .db.bootstrap import initialize
        initialize(engine(os.getenv("ADMIN_DATABASE_URL", "")))
        print("Schemas, validated constraints, approved views and least-privilege roles initialized.")
    elif args.command == "import":
        from .ingestion.importer import import_csvs
        last_printed = {}
        def progress(name, stats, complete):
            processed = stats.imported + stats.rejected + stats.duplicates
            if complete or processed - last_printed.get(name, 0) >= 50000:
                print(f"{name}: {processed:,} rows processed; {stats.imported:,} imported, {stats.rejected:,} rejected, {stats.duplicates:,} already present" + (" (complete)" if complete else ""), file=sys.stderr, flush=True)
                last_printed[name] = processed
        report = import_csvs(engine(os.getenv("ADMIN_DATABASE_URL", "")), args.folder,
                             "fixture" if args.fixture else "olist", progress=progress)
        print(report.model_dump_json(indent=2))
    else:
        from .services.store import Store
        from .analytics.query import QueryService
        store = Store(engine(settings.app_url))
        service = QueryService(engine(settings.analytics_url), settings, store)
        coverage = service.coverage()
        if args.command == "coverage":
            print(json.dumps(coverage, indent=2))
        else:
            from .retrieval.documents import LocalEmbeddings, index_documents
            print(json.dumps(index_documents(engine(os.getenv("ADMIN_DATABASE_URL", "")),
                LocalEmbeddings(settings.embedding_model), coverage), indent=2))


if __name__ == "__main__":
    main()
