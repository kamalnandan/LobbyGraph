"""Answers the five PoC questions directly from the in-memory graph.
Used to test the pipeline output before Neo4j is running; mirrors queries/poc_questions.cypher."""
from __future__ import annotations
from collections import defaultdict

from .model import Graph


def group_registrations(g: Graph, gid: str):
    gk = ("Group", gid)
    ents = [e.start for e in g.inc(gk, "PART_OF_GROUP")]
    regs = {e.start for ent in ents for e in g.inc(ent, "COVERS")}
    return ents, regs


def q1(g, gid):
    ents, _ = group_registrations(g, gid)
    rows = []
    for ent in ents:
        role = [e.props.get("role") for e in g.out(ent, "PART_OF_GROUP")][0]
        regs = [g.nodes[e.start].props["name"] for e in g.inc(ent, "COVERS")]
        rows.append({"entity": g.nodes[ent].props["name"], "country": g.nodes[ent].props["country"],
                     "role": role, "registrations": ", ".join(regs) or "-"})
    return sorted(rows, key=lambda r: (r["role"] != "parent", r["registrations"] == "-", r["entity"]))


def q2(g, gid, since=None):
    _, regs = group_registrations(g, gid)
    meetings = {e.end for r in regs for e in g.out(r, "ATTENDED")}
    if since:
        meetings = {m for m in meetings if g.nodes[m].props["date"] >= since}
    units, levels, per_reg = defaultdict(int), defaultdict(int), defaultdict(int)
    for m in meetings:
        p = g.nodes[m].props
        units[p.get("unit_acronym") or p["unit"]] += 1
        levels[p["level"]] += 1
        for e in g.inc(m, "ATTENDED"):
            if e.start in regs:
                per_reg[g.nodes[e.start].props["name"]] += 1
    return {"meetings": len(meetings), "by_level": dict(levels), "by_registration": dict(per_reg),
            "sum_of_registrations": sum(per_reg.values()),
            "by_unit": dict(sorted(units.items(), key=lambda x: -x[1]))}


def q5(g, gid):
    _, regs = group_registrations(g, gid)
    rows = {}
    for r in regs:
        for e in g.out(r, "MEMBER_OF"):
            a = g.nodes[e.end].props
            row = rows.setdefault(e.end, {"association": a["name"], "resolved": a.get("resolved"),
                                          "cost": a.get("cost_band"), "fte": a.get("fte"), "via": set(),
                                          "matched_by": e.prov.match_method})
            row["via"].add(g.nodes[r].props["name"])
    out = sorted(rows.values(), key=lambda r: (not r["resolved"], -(r["fte"] or 0)))
    for r in out:
        r["via"] = ", ".join(sorted(r["via"]))
    return out


def q8(g, gid):
    gk = ("Group", gid)
    ents, regs = group_registrations(g, gid)
    scope = {gk, *ents, *regs}
    rows = []
    for d in g.by_label("DataIssue"):
        if any(e.end in scope for e in g.out((d.label, d.key), "CONCERNS")):
            rows.append({"issue": d.key, **{k: d.props[k] for k in ("type", "severity", "status", "description")}})
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    return sorted(rows, key=lambda r: order.get(r["severity"], 9))


def q11(g, gid):
    _, regs = group_registrations(g, gid)
    rows = []
    for r in regs:
        for h in g.out(r, "HIRED"):
            others = [e.start for e in g.inc(h.end, "HIRED") if e.start != r]
            rows.append({"client": g.nodes[r].props["name"], "consultancy": g.nodes[h.end].props["name"],
                         "band": h.props.get("amount_band"), "other_clients": len(set(others)),
                         "examples": ", ".join(sorted({g.nodes[o].props["name"] for o in others})[:6])})
    return sorted(rows, key=lambda r: -r["other_clients"])
