"""Builds the LobbyGraph graph from the raw sources, following the schema in the product brief."""
from __future__ import annotations
import pandas as pd

from .model import Graph, Provenance
from .parsing import (normalise, parse_band, parse_intermediaries, parse_memberships,
                      name_variants, split_list, find_exclusions)
from .sources import COL, REGISTER_SOURCE, REGISTER_URL

ASSOCIATION_CATEGORIES = {"Trade and business associations", "Trade unions and professional associations",
                          "Non-governmental organisations, platforms and networks and similar",
                          "Think tanks and research institutions", "Other organisations, public or mixed entities",
                          "Associations and networks of public authorities"}
CONSULTANCY_CATEGORIES = {"Professional consultancies", "Law firms", "Self-employed individuals"}


def _val(row, key):
    v = row.get(COL[key])
    return None if v is None or (isinstance(v, float) and pd.isna(v)) or str(v) == "nan" else v


class RegisterIndex:
    """Name and acronym index over the whole register, used to resolve free-text names."""

    def __init__(self, df: pd.DataFrame):
        self.df = df.set_index(COL["id"], drop=False)
        self.by_name, self.by_acr, self.by_compact = {}, {}, {}
        for rid, name, acr, cat in zip(df[COL["id"]], df[COL["name"]], df[COL["acronym"]], df[COL["category"]]):
            self.by_name.setdefault(normalise(name), []).append((rid, cat))
            self.by_compact.setdefault(normalise(name).replace(" ", ""), []).append((rid, cat))
            if isinstance(acr, str) and acr.strip():
                self.by_acr.setdefault(normalise(acr), []).append((rid, cat))

    def resolve(self, text: str, categories: set[str]):
        """Return (register_id, match_method, confidence) or (None, reason, None)."""
        for variant in name_variants(text):
            hits = [r for r, c in self.by_name.get(normalise(variant), []) if c in categories]
            if len(hits) == 1:
                return hits[0], f"exact name '{variant}'", "high"
        for variant in name_variants(text):   # 'Business Europe' = 'BUSINESSEUROPE'
            hits = [r for r, c in self.by_compact.get(normalise(variant).replace(" ", ""), []) if c in categories]
            if len(hits) == 1:
                return hits[0], f"name without spaces '{variant}'", "medium"
        for variant in name_variants(text):
            hits = [r for r, c in self.by_acr.get(normalise(variant), []) if c in categories]
            if len(hits) == 1:
                return hits[0], f"unique acronym '{variant}'", "medium"
            if len(hits) > 1:
                return None, f"ambiguous acronym '{variant}' ({len(hits)} matches)", None
        return None, "no match in register", None

    def row(self, rid):
        return self.df.loc[rid] if rid in self.df.index else None


def build(cfg: dict, register: pd.DataFrame, gleif, meetings: pd.DataFrame, attendance, run_date: str) -> tuple[Graph, list]:
    g = Graph()
    unresolved = []     # (registration id, kind, text, reason) -> turned into DataIssues by checks
    idx = RegisterIndex(register)
    reg_prov = lambda **kw: Provenance(source=REGISTER_SOURCE, source_url=REGISTER_URL, retrieved=run_date, **kw)
    gleif_prov = lambda lei, **kw: Provenance(source="GLEIF", source_url=f"https://search.gleif.org/#/record/{lei}",
                                              retrieved=gleif.get(lei)["retrieved"] if gleif.get(lei) else run_date, **kw)

    # ---- legal entities (GLEIF) ----------------------------------------------
    def entity_node(lei):
        e = gleif.get(lei)
        if not e:
            raise KeyError(f"LEI {lei} not in GLEIF cache")
        return g.add_node("LegalEntity", lei, name=e["legal_name"], country=e["country"], city=e.get("city"),
                          hq_city=e.get("hq_city"),
                          registered_as=e.get("registered_as"), status=e.get("status"),
                          lei_status=e.get("lei_status"), next_renewal=e.get("next_renewal"),
                          parent_status=e.get("ultimate_parent_exception"))

    # ---- groups and membership (derived from GLEIF ultimate parent) -----------
    for grp in cfg["groups"]:
        gk = g.add_node("Group", grp["id"], name=grp["name"], sector=grp.get("sector"), parent_lei=grp["parent_lei"])
        pk = entity_node(grp["parent_lei"])
        g.add_edge("PART_OF_GROUP", pk, gk, Provenance("LobbyGraph group rule", method="derived", retrieved=run_date,
                   match_method="group parent (configured)"), role="parent")
        for child in gleif.children_of(grp["parent_lei"]):
            ck = entity_node(child["lei"])
            g.add_edge("CONSOLIDATED_BY", ck, pk, gleif_prov(child["lei"], match_method="GLEIF Level 2"), level="ultimate")
            g.add_edge("PART_OF_GROUP", ck, gk, Provenance("LobbyGraph group rule", method="derived", retrieved=run_date,
                       match_method="ultimate accounting parent = group parent (GLEIF)"), role="subsidiary")

    for s in cfg.get("stakes", []):
        g.add_edge("HOLDS_STAKE", entity_node(s["holder_lei"]), entity_node(s["target_lei"]),
                   Provenance("Manual research", method="manual", confidence="medium", retrieved=run_date,
                              match_method="manual"), percentage=s.get("percentage"), evidence=s.get("evidence"))

    # ---- registrations of the covered groups ------------------------------------
    covered_ids = {r["register_id"]: r for r in cfg["registrations"]}
    for rid, rc in covered_ids.items():
        row = idx.row(rid)
        if row is None:
            unresolved.append((rid, "registration", rid, "register ID not in export"))
            continue
        low, high = parse_band(_val(row, "cost"))
        rk = g.add_node("LobbyRegistration", rid, name=_val(row, "name"), type="company",
                        category=_val(row, "category"), registered=_val(row, "registered"),
                        hq_city=_val(row, "hq_city"), hq_country=_val(row, "hq_country"),
                        cost_low=low, cost_high=high, cost_band=_val(row, "cost"),
                        fte=float(_val(row, "fte") or 0), people=int(float(_val(row, "people") or 0)),
                        fy_start=_val(row, "fy_start"), fy_end=_val(row, "fy_end"),
                        eu_grants=_val(row, "eu_grants"), scope_text=_val(row, "staff_text"),
                        resolved=True)
        g.add_edge("COVERS", rk, entity_node(rc["lei"]), reg_prov(method="manual", match_method="register ID mapped to LEI, checked on name and country"))
        for extra in rc.get("also_covers", []):
            g.add_edge("COVERS", rk, entity_node(extra["lei"]),
                       reg_prov(method="derived", confidence="medium", match_method="scope stated in free text"),
                       evidence=extra.get("evidence"))
        # EXCLUDES: register IDs the entry says are listed separately
        text = " ".join(str(_val(row, k) or "") for k in ("extra_text", "staff_text"))
        for other in find_exclusions(text):
            if other in covered_ids and other != rid:
                g.add_edge("EXCLUDES", rk, entity_node(covered_ids[other]["lei"]),
                           reg_prov(method="derived", match_method=f"register ID {other} cited as separately listed"))

        # MEMBER_OF: free-text memberships resolved to association registrations
        for name in parse_memberships(_val(row, "member_of")):
            target, how, conf = idx.resolve(name, ASSOCIATION_CATEGORIES)
            if target:
                arow = idx.row(target)
                alow, ahigh = parse_band(arow[COL["cost"]])
                ak = g.add_node("LobbyRegistration", target, name=arow[COL["name"]], type="association",
                                acronym=arow[COL["acronym"]] if isinstance(arow[COL["acronym"]], str) else None,
                                category=arow[COL["category"]], cost_low=alow, cost_high=ahigh,
                                cost_band=arow[COL["cost"]], fte=float(arow[COL["fte"]] or 0)
                                if str(arow[COL["fte"]]) != "nan" else None, resolved=True)
                g.add_edge("MEMBER_OF", rk, ak, reg_prov(method="derived", confidence=conf, match_method=how),
                           as_written=name)
            else:
                ak = g.add_node("LobbyRegistration", "UNRESOLVED:" + normalise(name), name=name,
                                type="association", resolved=False)
                g.add_edge("MEMBER_OF", rk, ak, reg_prov(method="derived", confidence="low", match_method=how),
                           as_written=name)
                unresolved.append((rid, "association", name, how))

        # HIRED: packed intermediaries field
        for it in parse_intermediaries(_val(row, "intermediaries")):
            target, how, conf = idx.resolve(it["name"], CONSULTANCY_CATEGORIES)
            key = target or "UNRESOLVED:" + normalise(it["name"])
            ck = g.add_node("LobbyRegistration", key, name=(idx.row(target)[COL["name"]] if target else it["name"]),
                            type="consultancy", category=(idx.row(target)[COL["category"]] if target else None),
                            resolved=bool(target))
            g.add_edge("HIRED", rk, ck, reg_prov(method="official" if target else "derived",
                       confidence=conf or "low", match_method=how),
                       amount_band=it["band"], amount_low=it["low"], amount_high=it["high"],
                       fy_start=_val(row, "fy_start"), fy_end=_val(row, "fy_end"))
            if not target:
                unresolved.append((rid, "consultancy", it["name"], how))

        # INTERESTED_IN: declared fields of interest and targeted EU files
        for f in split_list(_val(row, "fields"), seps=r","):
            tk = g.add_node("Topic", "field:" + normalise(f), name=f, type="field of interest")
            g.add_edge("INTERESTED_IN", rk, tk, reg_prov(match_method="declared field"))
        for f in split_list(_val(row, "files")):
            if len(f) <= 120:
                tk = g.add_node("Topic", "file:" + normalise(f), name=f, type="EU file (as written)")
                g.add_edge("INTERESTED_IN", rk, tk, reg_prov(match_method="declared EU file, free text"))

    # ---- other clients of the consultancies our groups hire (question 11) --------------
    hired = {e.end[1] for e in g.edges if e.type == "HIRED" and not e.end[1].startswith("UNRESOLVED:")}
    if hired:
        names = {}
        for rid2, packed, cat, nm, fys, fye in zip(register[COL["id"]], register[COL["intermediaries"]],
                                                   register[COL["category"]], register[COL["name"]],
                                                   register[COL["fy_start"]], register[COL["fy_end"]]):
            if rid2 in covered_ids or not isinstance(packed, str):
                continue
            for it in parse_intermediaries(packed):
                n = normalise(it["name"])
                if n not in names:
                    names[n] = idx.resolve(it["name"], CONSULTANCY_CATEGORIES)
                target, how, conf = names[n]
                if target in hired:
                    ck = g.add_node("LobbyRegistration", rid2, name=nm, category=cat,
                                    type="company" if cat == "Companies & groups" else "other", resolved=True)
                    g.add_edge("HIRED", ck, ("LobbyRegistration", target),
                               reg_prov(confidence=conf, match_method=how), amount_band=it["band"],
                               amount_low=it["low"], amount_high=it["high"], fy_start=fys, fy_end=fye)

    # ---- Commission meetings (cabinets and Directors-General) --------------------------
    covered = {k[1] for k in g.nodes if k[0] == "LobbyRegistration" and k[1] in covered_ids}
    mine = meetings[meetings["ids"].map(lambda ids: bool(covered.intersection(ids)))]
    for _, m in mine.iterrows():
        mk = g.add_node("Meeting", m["meeting_id"], date=m["date"], location=m["location"], subject=m["subject"],
                        level=m["level"], unit=m["unit"], unit_acronym=m.get("unit_acronym"),
                        attendees=len(m["ids"]), source_rows=int(m["source_rows"]))
        mprov = Provenance(m["source"], source_url=m["source_url"], retrieved=run_date,
                           match_method="register ID in meeting record")
        for rid2 in m["ids"]:
            if ("LobbyRegistration", rid2) not in g.nodes:
                row2 = idx.row(rid2)
                if row2 is None:
                    unresolved.append((m["meeting_id"], "meeting attendee", rid2, "register ID not in export"))
                    continue
                cat = row2[COL["category"]]
                g.add_node("LobbyRegistration", rid2, name=row2[COL["name"]], category=cat,
                           type={"Companies & groups": "company", "Professional consultancies": "consultancy"}.get(
                               cat, "association" if cat in ASSOCIATION_CATEGORIES else "other"), resolved=True)
            g.add_edge("ATTENDED", ("LobbyRegistration", rid2), mk, mprov)
        if m["subject"]:
            tk = g.add_node("Topic", "subject:" + normalise(m["subject"])[:120], name=m["subject"], type="meeting subject")
            g.add_edge("ABOUT", mk, tk, mprov)
        uk = g.add_node("Unit", normalise(m["unit"]), name=m["unit"], level=m["level"], acronym=m.get("unit_acronym"))
        g.add_edge("HELD_BY", mk, uk, mprov)
    if attendance is not None and len(mine):
        att = attendance[attendance["meeting_id"].isin(set(mine["meeting_id"]))]
        for _, o in att.iterrows():
            ok = g.add_node("Official", normalise(o["official"]), name=o["official"])
            uk = ("Unit", normalise(o["unit"]))
            p = Provenance("European Commission meeting records", retrieved=run_date, match_method="listed in meeting record")
            g.add_edge("WORKS_IN", ok, uk, p, title=o["title"])
            g.add_edge("WITH", ("Meeting", o["meeting_id"]), ok, p)
    return g, unresolved
