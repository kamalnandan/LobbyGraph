"""Data-quality checks. Each finding becomes a DataIssue node linked (CONCERNS) to the records involved."""
from __future__ import annotations
from collections import defaultdict
from itertools import count

from .model import Graph, Provenance
from .parsing import normalise

CITY_ALIASES = {"munchen": "munich", "muenchen": "munich", "koln": "cologne", "bruxelles": "brussels",
                "roma": "rome", "milano": "milan", "torino": "turin", "napoli": "naples", "firenze": "florence",
                "sevilla": "seville", "zaragoza": "saragossa", "lisboa": "lisbon", "warszawa": "warsaw",
                "krakow": "cracow", "praha": "prague", "wien": "vienna", "den haag": "the hague",
                "s gravenhage": "the hague", "antwerpen": "antwerp", "gent": "ghent", "kobenhavn": "copenhagen",
                "goteborg": "gothenburg", "athina": "athens", "bucuresti": "bucharest", "nurnberg": "nuremberg",
                "hannover": "hanover", "frankfurt am main": "frankfurt"}


def _city(c):
    n = normalise(c or "")
    return CITY_ALIASES.get(n, n)


def run_checks(g: Graph, unresolved: list, gleif, cfg: dict, run_date: str) -> list[dict]:
    issues, seq = [], count(1)
    prov = Provenance("LobbyGraph data checks", method="derived", retrieved=run_date, match_method="automated check")

    def issue(kind, severity, text, action, concerns, status="open"):
        iid = f"DQ{next(seq):03d}"
        k = g.add_node("DataIssue", iid, type=kind, severity=severity, description=text, action=action, status=status)
        for c in concerns:
            g.add_edge("CONCERNS", k, c, prov)
        issues.append({"id": iid, "type": kind, "severity": severity, "status": status, "description": text,
                       "action": action, "concerns": "; ".join(f"{l}:{key}" for l, key in concerns)})

    group_of = {}
    for e in g.edges:
        if e.type == "PART_OF_GROUP":
            group_of[e.start] = e.end

    regs_by_group = defaultdict(set)
    for e in g.edges:
        if e.type == "COVERS" and e.end in group_of:
            regs_by_group[group_of[e.end]].add(e.start)

    for gk, regs in regs_by_group.items():
        gname = g.nodes[gk].props["name"]
        parent = ("LegalEntity", g.nodes[gk].props["parent_lei"])
        parent_regs = [r for r in regs if any(e.end == parent for e in g.out(r, "COVERS"))]
        # 1. possible double counting between registrations of one group
        for pr in parent_regs:
            excluded = {e.end for e in g.out(pr, "EXCLUDES")}
            for other in regs - {pr}:
                main = [e.end for e in g.out(other, "COVERS") if e.prov.method == "manual"]
                for ent in main:
                    nm = g.nodes[ent].props["name"]
                    if ent in excluded:
                        issue("Double counting check", "info",
                              f"{gname}: {nm} has its own registration and the parent registration states it is excluded, so both can be added.",
                              "None; keep the EXCLUDES link under review each year", [pr, other, ent], status="resolved")
                    else:
                        issue("Possible double counting", "high",
                              f"{gname}: {nm} has its own registration, but the parent registration does not say whether its figures include it.",
                              "Read the parent's scope text; show the group total as an upper bound until checked", [pr, other, ent])
        # 2. mixed reporting periods inside a group
        ends = {g.nodes[r].props.get("fy_end") for r in regs}
        if len(ends) > 1:
            issue("Different reporting periods", "medium",
                  f"{gname}: registrations report different financial years ({', '.join(sorted(str(x) for x in ends))}).",
                  "Show the period next to every figure", list(regs))

    # 3. head office in the register vs legal address in GLEIF
    for r in g.by_label("LobbyRegistration"):
        rk = (r.label, r.key)
        for e in g.out(rk, "COVERS"):
            if e.prov.method != "manual":
                continue
            ent = g.nodes[e.end].props
            gleif_cities = {_city(ent.get("city")), _city(ent.get("hq_city"))} - {""}
            if r.props.get("hq_city") and gleif_cities and _city(r.props["hq_city"]) not in gleif_cities:
                issue("Head office differs by source", "low",
                      f"{r.props['name']}: register head office {r.props['hq_city']}, GLEIF legal address and headquarters {ent['city']}.",
                      "Use GLEIF for the legal address; keep the register value as declared", [rk, e.end])

    # 4. names that could not be resolved to a register entry
    gone = [(mid, text) for mid, kind, text, reason in unresolved if kind == "meeting attendee"]
    if gone:
        ids = sorted({t for _, t in gone})
        issue("Meeting attendees not in the register", "low",
              f"{len(ids)} organisation(s) listed in {len({m for m, _ in gone})} meeting(s) are no longer in the Transparency Register export, so their names are not shown: " + ", ".join(ids),
              "Look them up in older register exports or in LobbyFacts history", [("Meeting", m) for m, _ in gone if ("Meeting", m) in g.nodes][:50])
    by_reg = defaultdict(lambda: defaultdict(list))
    for rid, kind, text, reason in unresolved:
        if kind == "meeting attendee":
            continue
        by_reg[rid][kind].append(f"{text} ({reason})")
    for rid, kinds in by_reg.items():
        for kind, items in kinds.items():
            concerns = [("LobbyRegistration", rid)] if ("LobbyRegistration", rid) in g.nodes else []
            issue(f"Unresolved {kind} names", "medium",
                  f"{len(items)} {kind} name(s) could not be matched to a register entry: " + "; ".join(items),
                  "Review by hand and add to the alias list", concerns)

    # 4b. meetings listed more than once in the source (counted once here)
    for gk, regs in regs_by_group.items():
        ms = {e.end for r in regs for e in g.out(r, "ATTENDED")}
        dup = [m for m in ms if g.nodes[m].props.get("level") == "cabinet" and (g.nodes[m].props.get("source_rows") or 1) > 1]
        if dup:
            issue("Duplicate meeting rows in source", "low",
                  f"{g.nodes[gk].props['name']}: {len(dup)} meeting(s) appear more than once in the Commission's cabinet list; each is counted once.",
                  "Keep counting distinct meetings; report the duplicates to the publisher", [gk, *dup])
        seen = {}
        for m in ms:
            p = g.nodes[m].props
            k = (p.get("unit"), p.get("date"), tuple(sorted(e.start[1] for e in g.inc(m, "ATTENDED"))))
            seen.setdefault(k, []).append(m)
        near = [v for v in seen.values() if len(v) > 1]
        if near:
            issue("Possible duplicate meetings", "low",
                  f"{g.nodes[gk].props['name']}: {len(near)} case(s) of the same unit meeting the same organisations on the same day with different subject text.",
                  "Review by hand before counting them as separate meetings", [gk, *[m for v in near for m in v]])

    # 5. stakes without a percentage
    for e in g.edges:
        if e.type == "HOLDS_STAKE" and e.props.get("percentage") in (None, ""):
            issue("Stake size unknown", "low",
                  f"{g.nodes[e.start].props['name']} holds a stake in {g.nodes[e.end].props['name']}; percentage not recorded.",
                  "Take the percentage from the latest annual report", [e.start, e.end])

    # 6. partial ownership coverage in this run
    for grp in cfg["groups"]:
        total = gleif.meta.get("ultimate_children_total", {}).get(grp["parent_lei"])
        have = len(gleif.children_of(grp["parent_lei"]))
        if total and have < total:
            issue("Partial ownership data", "info",
                  f"{grp['name']}: {have} of {total} GLEIF subsidiaries loaded in this run.",
                  "Run the pipeline online to fetch every subsidiary", [("Group", grp["id"])])
    # 7. LEIs that are no longer kept up to date (lapsed, retired, ...)
    OK_LEI = {None, "", "ISSUED", "PENDING_TRANSFER", "PENDING_ARCHIVAL"}
    covered = {e.end for e in g.edges if e.type == "COVERS" and e.prov.method == "manual"}
    for ek in sorted(covered):
        p = g.nodes[ek].props
        if p.get("lei_status") not in OK_LEI:
            issue("LEI not up to date", "medium",
                  f"{p['name']}: its LEI has status {p['lei_status']} in GLEIF, so its name, address and parent may be out of date.",
                  "Check the company's latest annual report before relying on the GLEIF data", [ek])
    for grp in cfg["groups"]:
        gk = ("Group", grp["id"])
        members = [e.start for e in g.edges if e.type == "PART_OF_GROUP" and e.end == gk]
        stale = [m for m in members if m not in covered and g.nodes[m].props.get("lei_status") not in OK_LEI]
        if stale:
            names = sorted(g.nodes[m].props["name"] for m in stale)
            issue("Subsidiary LEIs not up to date", "info",
                  f"{grp['name']}: {len(stale)} of {len(members)} companies in the group have an LEI that is not up to date"
                  f" (for example lapsed): " + ", ".join(names[:15]) + (" ..." if len(names) > 15 else "") + ".",
                  "Treat their GLEIF details as possibly outdated; group membership still follows the last parent report",
                  [gk, *stale[:50]])

    # 8. companies with their own lobby registration but no parent link in GLEIF
    in_group = {e.start for e in g.edges if e.type == "PART_OF_GROUP"}
    for ek in sorted(covered - in_group):
        p = g.nodes[ek].props
        why = p.get("parent_status") or "no parent and no reason reported"
        stake = any(e.type == "HOLDS_STAKE" and e.end == ek for e in g.edges)
        issue("Parent link missing in GLEIF", "info" if stake else "medium",
              f"{p['name']} has its own lobby registration, but GLEIF does not link it to any group parent ({why})."
              + (" It is shown through a recorded stake instead." if stake else ""),
              "Check ownership in the annual report; add a stake or a manual group link if needed", [ek])
    return issues
