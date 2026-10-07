"""Download the public original Kaggle archive and validate it before placement."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil
import tempfile
import zipfile
import requests
from olist_agent.db.schema import SPECS
from olist_agent.ingestion.importer import inspect_files


def main():
    root = Path(__file__).resolve().parents[1]
    folder = root / "data" / "raw"
    folder.mkdir(parents=True, exist_ok=True)
    expected = {spec[0] for spec in SPECS.values()}
    if all((folder / name).is_file() for name in expected):
        inspect_files(folder)
        print("All nine CSVs already exist and their headers validate; preserved existing files.")
        return
    url = "https://www.kaggle.com/api/v1/datasets/download/olistbr/brazilian-ecommerce"
    with tempfile.TemporaryDirectory(dir=root / ".runtime") as temporary:
        staging = Path(temporary)
        archive = staging / "olist.zip"
        digest = sha256()
        size = 0
        with requests.get(url, stream=True, timeout=(15, 60)) as response:
            response.raise_for_status()
            with archive.open("wb") as handle:
                for block in response.iter_content(1024 * 1024):
                    size += len(block)
                    if size > 500 * 1024 * 1024:
                        raise ValueError("Archive exceeds 500 MB limit")
                    digest.update(block)
                    handle.write(block)
        if not zipfile.is_zipfile(archive):
            raise ValueError("Kaggle returned no ZIP archive; an authenticated Kaggle download may be required")
        unpacked = staging / "raw"
        unpacked.mkdir()
        with zipfile.ZipFile(archive) as handle:
            members = {Path(info.filename).name: info for info in handle.infolist() if Path(info.filename).name in expected}
            if set(members) != expected:
                raise ValueError("Archive does not contain all nine expected source CSVs")
            for name, member in members.items():
                if member.file_size > 400 * 1024 * 1024:
                    raise ValueError("CSV exceeds expected size bound")
                with handle.open(member) as source, (unpacked / name).open("wb") as target:
                    shutil.copyfileobj(source, target)
        inspect_files(unpacked)
        for name in sorted(expected):
            target = folder / name
            if target.exists():
                if sha256(target.read_bytes()).digest() != sha256((unpacked / name).read_bytes()).digest():
                    raise ValueError(f"Existing {name} differs; refusing to overwrite user data")
        for name in sorted(expected):
            if not (folder / name).exists():
                shutil.move(str(unpacked / name), str(folder / name))
        manifest = {"source": "https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce", "download_url": url,
                    "downloaded_at": datetime.now(timezone.utc).isoformat(), "archive_bytes": size,
                    "archive_sha256": digest.hexdigest(),
                    "files": {name: {"bytes": (folder / name).stat().st_size,
                        "sha256": sha256((folder / name).read_bytes()).hexdigest()} for name in sorted(expected)}}
        (folder / "download_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"Downloaded original Kaggle ZIP ({size:,} bytes); validated and placed nine real Olist CSVs in data/raw.")
        print(f"Archive SHA256: {digest.hexdigest()}")


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    (root / ".runtime").mkdir(exist_ok=True)
    main()
