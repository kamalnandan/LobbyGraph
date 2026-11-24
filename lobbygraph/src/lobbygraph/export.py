"""Writes the graph as CSV files plus a Cypher script, and loads it into Neo4j."""
from __future__ import annotations
import csv
import json
from collections import defaultdict
from pathlib import Path

from .model import Graph


def _clean(v):
    """Neo4j accepts only plain Python values: convert numpy types, drop NaN."""
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        v = v.item()
    if isinstance(v, float) and v != v:
        return None
    return v

KEYS = {"Group": "id", "LegalEntity": "lei", "LobbyRegistration": "register_id", "Meeting": "meeting_id",
        "Official": "official_id", "Unit": "unit_id", "Topic": "topic_id", "DataIssue": "issue_id"}


def write_csv(g: Graph, out: Path, issues: list[dict]):
    out.mkdir(parents=True, exist_ok=True)
    by_label = defaultdict(list)
    for n in g.nodes.values():
        by_label[n.label].append({KEYS[n.label]: n.key, **n.props})
    for label, rows in by_label.items():
        cols = list(dict.fromkeys(c for r in rows for c in r))
        with open(out / f"nodes_{label}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
    erows = [g.edge_dict(e) for e in g.edges]
    cols = list(dict.fromkeys(c for r in erows for c in r))
    with open(out / "edges.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(erows)
    if issues:
        with open(out / "data_issues.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(issues[0]))
            w.writeheader()
            w.writerows(issues)


def constraints() -> list[str]:
    return [f"CREATE CONSTRAINT {l.lower()}_key IF NOT EXISTS FOR (n:{l}) REQUIRE n.{k} IS UNIQUE"
            for l, k in KEYS.items()]


def load_neo4j(g: Graph, uri: str, user: str, password: str, database: str = "neo4j", batch: int = 500,
               rebuild: bool = True):
    """Load with UNWIND batches. The graph is derived data, so by default it is rebuilt from scratch.
    Credentials come from Key Vault in Azure."""
    from neo4j import GraphDatabase
    with GraphDatabase.driver(uri, auth=(user, password)) as driver, driver.session(database=database) as s:
        if rebuild:
            s.run("MATCH (n) DETACH DELETE n")  # small graph; batch this for large ones
        for c in constraints():
            s.run(c)
        nodes = defaultdict(list)
        for n in g.nodes.values():
            nodes[n.label].append({"key": n.key, "props": {k: _clean(v) for k, v in n.props.items() if _clean(v) is not None}})
        for label, rows in nodes.items():
            for i in range(0, len(rows), batch):
                s.run(f"UNWIND $rows AS r MERGE (n:{label} {{{KEYS[label]}: r.key}}) SET n += r.props",
                      rows=rows[i:i + batch])
        edges = defaultdict(list)
        for e in g.edges:
            d = g.edge_dict(e)
            props = {k: _clean(v) for k, v in d.items() if k not in ("type", "start_label", "start_key", "end_label", "end_key") and _clean(v) is not None}
            edges[(e.type, e.start[0], e.end[0])].append({"a": e.start[1], "b": e.end[1], "props": props})
        for (t, la, lb), rows in edges.items():
            for i in range(0, len(rows), batch):
                s.run(f"UNWIND $rows AS r MATCH (a:{la} {{{KEYS[la]}: r.a}}) MATCH (b:{lb} {{{KEYS[lb]}: r.b}}) "
                      f"CREATE (a)-[x:{t}]->(b) SET x = r.props", rows=rows[i:i + batch])


def summary(g: Graph) -> dict:
    nodes, edges = defaultdict(int), defaultdict(int)
    for n in g.nodes.values():
        nodes[n.label] += 1
    for e in g.edges:
        edges[e.type] += 1
    return {"nodes": dict(nodes), "edges": dict(edges), "total_nodes": len(g.nodes), "total_edges": len(g.edges)}
