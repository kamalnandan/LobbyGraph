"""Entry point for the Azure Container Apps Job (the loader).

1. Downloads the raw source files from Blob Storage (container 'raw'), using the managed identity.
2. Runs the pipeline: fetches every subsidiary from GLEIF, builds the graph, runs the checks.
3. Loads the graph into Neo4j.
4. Uploads the output tables and the run summary to Blob Storage (container 'dumps', folder runs/<timestamp>/).

If STORAGE_ACCOUNT is not set, the job reads the raw files that were built into the image
(data/raw/transparency_register/ and data/raw/meetings/) and skips the upload step. This is the PoC mode for
subscriptions where a policy blocks public network access to storage accounts.

Environment variables:
  STORAGE_ACCOUNT   optional; name of the storage account, e.g. stacclobbygraphdev
  AZURE_CLIENT_ID   client ID of the user-assigned managed identity
  NEO4J_URI         e.g. bolt://ca-neo4j-lobbygraph-dev:7687
  NEO4J_USER        neo4j
  NEO4J_PASSWORD    from a Container Apps secret
  RAW_CONTAINER     optional, default 'raw'
  OUT_CONTAINER     optional, default 'dumps'
  GLEIF_ONLINE      optional, default 'true'
"""
from __future__ import annotations
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .cli import main as run_pipeline, ROOT

RAW = ROOT / "data" / "raw"


def log(msg):
    print(f"[loader {datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


def latest(blobs, prefix, contains=None, suffixes=(".xls", ".xlsx", ".csv")):
    cands = [b for b in blobs if b.name.startswith(prefix) and b.name.lower().endswith(suffixes)
             and (contains is None or contains.lower() in b.name.lower())]
    if not cands:
        return None
    return max(cands, key=lambda b: b.last_modified)


def download(container, blob, target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "wb") as f:
        container.download_blob(blob.name).readinto(f)
    log(f"downloaded {blob.name} ({blob.size / 1e6:.1f} MB)")
    return target


def local_latest(folder: Path, contains=None):
    files = [p for p in folder.glob("*") if p.suffix.lower() in (".xls", ".xlsx", ".csv")
             and (contains is None or contains.lower() in p.name.lower())]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def run_local():
    reg = local_latest(RAW / "transparency_register")
    cab = local_latest(RAW / "meetings", "Commission representatives")
    dg = local_latest(RAW / "meetings", "Directors-General")
    missing = [n for n, p in [("data/raw/transparency_register/<register export>", reg),
                              ("data/raw/meetings/<...Commission representatives...>", cab),
                              ("data/raw/meetings/<...Directors-General...>", dg)] if p is None]
    if missing:
        log("missing input file(s) in the image: " + "; ".join(missing))
        sys.exit(2)
    for p in (reg, cab, dg):
        log(f"using built-in file {p.relative_to(ROOT)} ({p.stat().st_size / 1e6:.1f} MB)")
    args = ["--register", str(reg), "--cabinet-meetings", str(cab), "--dg-meetings", str(dg),
            "--out", "/tmp/out", "--neo4j"]
    if os.environ.get("GLEIF_ONLINE", "true").lower() == "true":
        args.append("--online")
    run_pipeline(args)
    log("graph loaded into Neo4j")


def main():
    account = os.environ.get("STORAGE_ACCOUNT")
    if not account:
        log("no STORAGE_ACCOUNT set: using the files built into the image")
        return run_local()
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient
    svc = BlobServiceClient(f"https://{account}.blob.core.windows.net", credential=DefaultAzureCredential())
    raw = svc.get_container_client(os.environ.get("RAW_CONTAINER", "raw"))
    blobs = list(raw.list_blobs())
    log(f"{len(blobs)} file(s) in container '{raw.container_name}'")

    reg = latest(blobs, "transparency_register/")
    cab = latest(blobs, "meetings/", contains="Commission representatives")
    dg = latest(blobs, "meetings/", contains="Directors-General")
    missing = [n for n, b in [("transparency_register/<register export>", reg),
                              ("meetings/<...Commission representatives...>", cab),
                              ("meetings/<...Directors-General...>", dg)] if b is None]
    if missing:
        log("missing input file(s): " + "; ".join(missing))
        sys.exit(2)

    reg_path = download(raw, reg, RAW / "transparency_register" / Path(reg.name).name)
    cab_path = download(raw, cab, RAW / "meetings" / "cabinet_meetings.xlsx")
    dg_path = download(raw, dg, RAW / "meetings" / "dg_meetings.xlsx")

    args = ["--register", str(reg_path), "--cabinet-meetings", str(cab_path), "--dg-meetings", str(dg_path),
            "--out", str(ROOT / "out"), "--neo4j"]
    if os.environ.get("GLEIF_ONLINE", "true").lower() == "true":
        args.append("--online")
    log("running pipeline: " + " ".join(a for a in args if not a.startswith("/")))
    run_pipeline(args)
    log("graph loaded into Neo4j")

    out = svc.get_container_client(os.environ.get("OUT_CONTAINER", "dumps"))
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    for f in sorted((ROOT / "out").glob("*")):
        if f.is_file():
            with open(f, "rb") as fh:
                out.upload_blob(f"runs/{stamp}/{f.name}", fh, overwrite=True)
    gleif_cache = ROOT / "data/raw/gleif/gleif_cache.json"
    with open(gleif_cache, "rb") as fh:
        out.upload_blob(f"runs/{stamp}/gleif_cache.json", fh, overwrite=True)
    log(f"outputs uploaded to '{out.container_name}/runs/{stamp}/'")


if __name__ == "__main__":
    main()
