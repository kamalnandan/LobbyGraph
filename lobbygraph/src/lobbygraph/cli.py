"""Command line entry point:  python -m lobbygraph.cli --register <export> [--online] [--neo4j]"""
from __future__ import annotations
import argparse
import json
import os
from datetime import date
from pathlib import Path

import yaml

from .sources import read_register, Gleif, read_commission_meetings
from .transform import build
from .checks import run_checks
from .export import write_csv, load_neo4j, summary

ROOT = Path(__file__).resolve().parents[2]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the LobbyGraph graph")
    ap.add_argument("--config", default=ROOT / "config/groups.yaml")
    ap.add_argument("--register", required=True, help="Transparency Register export (.xlsx/.csv/.pkl)")
    ap.add_argument("--gleif-cache", default=ROOT / "data/raw/gleif/gleif_cache.json")
    ap.add_argument("--cabinet-meetings", default=ROOT / "data/raw/meetings/cabinet_meetings_2024_2029.xlsx")
    ap.add_argument("--dg-meetings", default=ROOT / "data/raw/meetings/dg_meetings.xlsx")
    ap.add_argument("--out", default=ROOT / "out")
    ap.add_argument("--online", action="store_true", help="fetch every subsidiary from the GLEIF API")
    ap.add_argument("--neo4j", action="store_true", help="load into Neo4j (NEO4J_URI/USER/PASSWORD from env)")
    a = ap.parse_args(argv)

    run_date = date.today().isoformat()
    cfg = yaml.safe_load(Path(a.config).read_text())
    register = read_register(a.register, cache_dir=ROOT / "data/interim")
    gleif = Gleif(a.gleif_cache, online=a.online)
    if a.online:
        for grp in cfg["groups"]:
            gleif.fetch_group(grp["parent_lei"], run_date)
        # entities referenced in the config but outside the groups (e.g. RTE, a stake target)
        refs = [r["lei"] for r in cfg["registrations"]]
        refs += [x["lei"] for r in cfg["registrations"] for x in r.get("also_covers", [])]
        refs += [s[k] for s in cfg.get("stakes", []) for k in ("holder_lei", "target_lei")]
        for lei in refs:
            gleif.fetch_entity(lei, run_date)
        gleif.save()
    meetings, attendance = read_commission_meetings(a.cabinet_meetings, a.dg_meetings)

    g, unresolved = build(cfg, register, gleif, meetings, attendance, run_date)
    issues = run_checks(g, unresolved, gleif, cfg, run_date)
    out = Path(a.out)
    write_csv(g, out, issues)
    s = summary(g)
    (out / "summary.json").write_text(json.dumps(s, indent=1))
    if a.neo4j:
        load_neo4j(g, os.environ["NEO4J_URI"], os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])
    print(json.dumps(s, indent=1))
    return g, issues


if __name__ == "__main__":
    main()
