"""LobbyGraph PoC web app: the whole company behind EU lobbying, with a source for every link.
Reads the Neo4j graph built by the loader. All queries run in READ mode."""
import hmac
import os
import re
import time
from datetime import date

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from neo4j import GraphDatabase, READ_ACCESS

from viz import (PARENT, REST, build_network, heatmap_spec, network_html, parent_vs_group_spec,
                 ranking_spec)

st.set_page_config(page_title="LobbyGraph", page_icon="🔎", layout="wide")


@st.cache_resource
def get_driver():
    return GraphDatabase.driver(os.environ.get("NEO4J_URI", "bolt://neo4j:7687"),
                                auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"]))


@st.cache_data(ttl=600, show_spinner=False)
def run(query: str, **params) -> list[dict]:
    with get_driver().session(default_access_mode=READ_ACCESS) as s:
        return [r.data() for r in s.run(query, **params)]


def euro(v):
    return "–" if v in (None, 0) else f"€{v / 1e6:,.2f} m" if v >= 1e6 else f"€{v:,.0f}"


def fmt_cost(v):
    """'4 500 000 - 4 999 999' -> '€4.50 m – €5.00 m'; '7822869' -> '€7.82 m'; anything else unchanged."""
    if v is None or (isinstance(v, float) and v != v):
        return None
    parts = [re.sub(r"[\s\u00a0]", "", p) for p in str(v).split("-")]
    if parts and all(p.replace(".", "", 1).isdigit() for p in parts):
        return " – ".join(euro(float(p)) for p in parts)
    return v


LABELS = {
    "registration": "Registration", "register_id": "Register ID", "covers_entity": "Covers company",
    "yearly_cost_eur": "Declared cost / year", "lobbyists_fte": "Lobbyists (FTE)", "financial_year_end": "Financial year end",
    "commission_unit": "Commission unit", "group_registrations": "Group registration(s)", "also_present": "Also present",
    "association": "Association / organisation", "found_in_register": "In register", "association_cost_eur": "Its declared cost / year",
    "association_fte": "Its lobbyists (FTE)", "member_via": "Member via", "matched_by": "How we matched it",
    "consultancy": "Consultancy", "hired_by": "Hired by", "paid_eur": "Paid by the group",
    "other_clients": "Other clients", "other_clients_examples": "Other clients (examples)",
    "entity": "Company", "lei": "LEI", "what_we_do": "What we do about it", "source": "Source",
    "topic": "Topic", "topics": "Topics", "meetings": "Meetings", "evidence": "Text it came from",
    "declared_by": "Declared in register by", "group": "Company group",
}
COST_COLUMNS = ("yearly_cost_eur", "association_cost_eur", "paid_eur")


def show(rows, links=(), lists=(), height=None):
    if not rows:
        st.info("Nothing found in the sources for this group.")
        return
    df = pd.DataFrame(rows)
    for c in lists:
        if c in df:
            df[c] = df[c].map(lambda v: ", ".join(x for x in v if x) if isinstance(v, list) else v)
    for c in COST_COLUMNS:
        if c in df:
            df[c] = df[c].map(fmt_cost)
    names = {c: LABELS.get(c, c.replace("_", " ").capitalize()) for c in df.columns}
    df = df.rename(columns=names)
    cfg = {names[c]: st.column_config.LinkColumn(names[c], display_text="source") for c in links if c in names}
    kw = {"height": height} if height else {}
    st.dataframe(df, width="stretch", hide_index=True, column_config=cfg, **kw)


def chart(df: pd.DataFrame, spec: dict):
    """Vega-Lite chart at the full width of the page."""
    st.vega_lite_chart(df, {"width": "container", **spec})


# ---------------------------------------------------------------- sign-in
# User ID = the visitor's email address; password = that email + APP_PASSWORD_SUFFIX (set in .env).
# Optional ALLOWED_EMAILS (comma-separated) limits sign-in to invited people. No suffix set = nobody gets in.
SUFFIX = os.environ.get("APP_PASSWORD_SUFFIX", "")
ALLOWED = {e.strip().lower() for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def require_login():
    if st.session_state.get("user"):
        return
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        st.title("LobbyGraph")
        st.caption("See the whole company behind EU lobbying, with a source for every link.")
        if not SUFFIX:
            st.error("Sign-in is not configured yet.")
            st.stop()
        with st.form("login"):
            email = st.text_input("User ID (your email address)")
            pw = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", type="primary")
        if submitted:
            e = email.strip().lower()
            ok = (bool(EMAIL_RE.match(e)) and (not ALLOWED or e in ALLOWED)
                  and hmac.compare_digest(pw.strip().lower().encode(), (e + SUFFIX).lower().encode()))
            if ok:
                st.session_state["user"] = e
                print(f"[app] sign-in ok: {e}", flush=True)
                st.rerun()
            print(f"[app] sign-in failed: {e or '(empty)'}", flush=True)
            time.sleep(1.5)
            st.error("Email or password is not correct.")
    st.stop()


# ---------------------------------------------------------------- queries
Q_GROUPS = "MATCH (g:Group) RETURN g.id AS id, g.name AS name, g.parent_lei AS parent_lei ORDER BY g.name"

Q_FRESH = "MATCH ()-[x:COVERS]->() RETURN max(x.retrieved) AS retrieved"

Q_OVERVIEW = """
MATCH (g:Group {id: $gid})<-[:PART_OF_GROUP]-(e:LegalEntity)
WITH g, count(e) AS entities, count(DISTINCT e.country) AS countries
OPTIONAL MATCH (g)<-[:PART_OF_GROUP]-(:LegalEntity)<-[:COVERS]-(r:LobbyRegistration)
WITH entities, countries, collect(DISTINCT r) AS regs
RETURN entities, countries, size(regs) AS registrations,
       reduce(s = 0.0, r IN regs | s + coalesce(toFloat(r.cost_low), 0.0)) AS cost_low,
       reduce(s = 0.0, r IN regs | s + coalesce(toFloat(r.cost_high), 0.0)) AS cost_high,
       reduce(s = 0.0, r IN regs | s + coalesce(toFloat(r.fte), 0.0)) AS fte"""

Q_MEETING_COUNT = """
MATCH (g:Group {id: $gid})<-[:PART_OF_GROUP]-(:LegalEntity)<-[:COVERS]-(:LobbyRegistration)-[:ATTENDED]->(m:Meeting)
WHERE m.date >= $since
RETURN count(DISTINCT m) AS meetings"""

Q_REGISTRATIONS = """
MATCH (g:Group {id: $gid})<-[:PART_OF_GROUP]-(e:LegalEntity)<-[c:COVERS]-(r:LobbyRegistration)
WITH r, collect(DISTINCT e.name) AS covers_entity, collect(DISTINCT c.source_url)[0] AS source
RETURN r.name AS registration, r.register_id AS register_id, covers_entity,
       r.cost_band AS yearly_cost_eur, r.fte AS lobbyists_fte, r.fy_end AS financial_year_end, source
ORDER BY toFloat(r.cost_high) DESC"""

Q_ENTITIES = """
MATCH (g:Group {id: $gid})<-[p:PART_OF_GROUP]-(e:LegalEntity)
OPTIONAL MATCH (r:LobbyRegistration)-[:COVERS]->(e)
WITH e, p, collect(DISTINCT r.name) AS registrations
RETURN e.name AS entity, e.country AS country, e.lei AS lei, p.role AS role, registrations,
       'https://search.gleif.org/#/record/' + e.lei AS source
ORDER BY CASE role WHEN 'parent' THEN 0 ELSE 1 END, size(registrations) DESC, entity"""

Q_MEETINGS = """
MATCH (g:Group {id: $gid})<-[:PART_OF_GROUP]-(:LegalEntity)<-[:COVERS]-(r:LobbyRegistration)-[a:ATTENDED]->(m:Meeting)
WHERE m.date >= $since
WITH m, collect(DISTINCT r.name) AS group_registrations, collect(DISTINCT a.source_url)[0] AS source
OPTIONAL MATCH (m)-[:HELD_BY]->(u:Unit)
OPTIONAL MATCH (m)-[:WITH]->(o:Official)
OPTIONAL MATCH (other:LobbyRegistration)-[:ATTENDED]->(m) WHERE NOT other.name IN group_registrations
OPTIONAL MATCH (m)-[:ON_THEME]->(t:Theme)
RETURN m.date AS date, coalesce(u.acronym, u.name) AS commission_unit, m.level AS level, m.subject AS subject,
       collect(DISTINCT t.name) AS topics, group_registrations, collect(DISTINCT o.name) AS officials,
       collect(DISTINCT other.name) AS also_present, source
ORDER BY date DESC"""

Q_THEMES = "MATCH (t:Theme) RETURN t.theme_id AS id, t.name AS name ORDER BY name"

Q_THEME_MEETINGS = """
MATCH (g:Group {id: $gid})<-[:PART_OF_GROUP]-(:LegalEntity)<-[:COVERS]-(:LobbyRegistration)-[:ATTENDED]->(m:Meeting)
WHERE m.date >= $since
WITH DISTINCT m
OPTIONAL MATCH (m)-[:ON_THEME]->(t:Theme)
RETURN coalesce(t.name, 'General / no topic found') AS topic, count(DISTINCT m) AS meetings
ORDER BY meetings DESC"""

Q_THEME_DECLARED = """
MATCH (g:Group {id: $gid})<-[:PART_OF_GROUP]-(:LegalEntity)<-[:COVERS]-(r:LobbyRegistration)-[x:ON_THEME]->(t:Theme)
RETURN DISTINCT t.name AS topic, r.name AS registration, x.match_method AS matched_by, x.evidence AS evidence,
       x.source_url AS source
ORDER BY topic, registration"""

Q_THEME_RANK = """
MATCH (g:Group), (t:Theme {theme_id: $tid})
OPTIONAL MATCH (g)<-[:PART_OF_GROUP]-(:LegalEntity)<-[:COVERS]-(:LobbyRegistration)-[:ATTENDED]->(m:Meeting)-[:ON_THEME]->(t)
WHERE m.date >= $since
WITH g, t, count(DISTINCT m) AS meetings
OPTIONAL MATCH (g)<-[:PART_OF_GROUP]-(:LegalEntity)<-[:COVERS]-(r:LobbyRegistration)-[:ON_THEME]->(t)
RETURN g.name AS group, meetings, collect(DISTINCT r.name) AS declared_by
ORDER BY meetings DESC, group"""

Q_ASSOCIATIONS = """
MATCH (g:Group {id: $gid})<-[:PART_OF_GROUP]-(:LegalEntity)<-[:COVERS]-(r:LobbyRegistration)-[mo:MEMBER_OF]->(a:LobbyRegistration)
RETURN a.name AS association, coalesce(a.resolved, false) AS found_in_register, a.cost_band AS association_cost_eur,
       a.fte AS association_fte, collect(DISTINCT r.name) AS member_via, collect(DISTINCT mo.match_method)[0] AS matched_by,
       collect(DISTINCT mo.source_url)[0] AS source
ORDER BY found_in_register DESC, toFloat(association_fte) DESC"""

Q_CONSULTANCIES = """
MATCH (g:Group {id: $gid})<-[:PART_OF_GROUP]-(:LegalEntity)<-[:COVERS]-(r:LobbyRegistration)-[h:HIRED]->(c:LobbyRegistration)
OPTIONAL MATCH (other:LobbyRegistration)-[:HIRED]->(c) WHERE other <> r
RETURN c.name AS consultancy, r.name AS hired_by, h.amount_band AS paid_eur, h.fy_end AS financial_year_end,
       count(DISTINCT other) AS other_clients, collect(DISTINCT other.name)[0..8] AS other_clients_examples,
       h.source_url AS source
ORDER BY other_clients DESC"""

Q_ISSUES = """
MATCH (g:Group {id: $gid})
MATCH (d:DataIssue)-[:CONCERNS]->(x)
WHERE x = g
   OR (x)-[:PART_OF_GROUP]->(g)
   OR (x:LobbyRegistration)-[:COVERS]->(:LegalEntity)-[:PART_OF_GROUP]->(g)
   OR (x)<-[:MEMBER_OF|HIRED]-(:LobbyRegistration)-[:COVERS]->(:LegalEntity)-[:PART_OF_GROUP]->(g)
   OR (x:Meeting)<-[:ATTENDED]-(:LobbyRegistration)-[:COVERS]->(:LegalEntity)-[:PART_OF_GROUP]->(g)
RETURN DISTINCT d.issue_id AS issue, d.severity AS severity, d.type AS type, d.description AS description,
       d.action AS what_we_do, d.status AS status
ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 WHEN 'low' THEN 2 ELSE 3 END, issue"""

# ---- all groups: ranking (E3), group vs parent (E4), topic heatmap
Q_RANK = """
MATCH (g:Group)
OPTIONAL MATCH (g)<-[:PART_OF_GROUP]-(:LegalEntity)<-[:COVERS]-(r:LobbyRegistration)
WITH g, collect(DISTINCT r) AS regs
OPTIONAL MATCH (g)<-[:PART_OF_GROUP]-(:LegalEntity)<-[:COVERS]-(:LobbyRegistration)-[:ATTENDED]->(m:Meeting)
WHERE m.date >= $since
WITH g, regs, count(DISTINCT m) AS meetings
RETURN g.id AS gid, g.name AS group, meetings, size(regs) AS registrations,
       reduce(s = 0.0, r IN regs | s + coalesce(toFloat(r.cost_low), 0.0)) AS cost_low,
       reduce(s = 0.0, r IN regs | s + coalesce(toFloat(r.cost_high), 0.0)) AS cost_high,
       reduce(s = 0.0, r IN regs | s + coalesce(toFloat(r.fte), 0.0)) AS fte"""

Q_PARENT_ONLY = """
MATCH (g:Group)<-[:PART_OF_GROUP {role: 'parent'}]-(p:LegalEntity)
OPTIONAL MATCH (p)<-[:COVERS]-(r:LobbyRegistration)
WITH g, collect(DISTINCT r) AS regs
OPTIONAL MATCH (g)<-[:PART_OF_GROUP {role: 'parent'}]-(:LegalEntity)<-[:COVERS]-(:LobbyRegistration)-[:ATTENDED]->(m:Meeting)
WHERE m.date >= $since
WITH g, regs, count(DISTINCT m) AS meetings
RETURN g.id AS gid, meetings AS p_meetings, size(regs) AS p_registrations,
       reduce(s = 0.0, r IN regs | s + coalesce(toFloat(r.cost_low), 0.0)) AS p_cost_low,
       reduce(s = 0.0, r IN regs | s + coalesce(toFloat(r.cost_high), 0.0)) AS p_cost_high,
       reduce(s = 0.0, r IN regs | s + coalesce(toFloat(r.fte), 0.0)) AS p_fte"""

Q_HEAT = """
MATCH (g:Group)<-[:PART_OF_GROUP]-(:LegalEntity)<-[:COVERS]-(:LobbyRegistration)-[:ATTENDED]->(m:Meeting)-[:ON_THEME]->(t:Theme)
WHERE m.date >= $since
RETURN g.name AS group, t.name AS topic, count(DISTINCT m) AS meetings"""

# ---- one group: network picture (E5)
Q_NET_REGS = """
MATCH (g:Group {id: $gid})<-[:PART_OF_GROUP]-(e:LegalEntity)<-[:COVERS]-(r:LobbyRegistration)
RETURN r.register_id AS rid, r.name AS name, r.fte AS fte, collect(DISTINCT e.name) AS entities"""

Q_NET_ASSOC = """
MATCH (g:Group {id: $gid})<-[:PART_OF_GROUP]-(:LegalEntity)<-[:COVERS]-(r:LobbyRegistration)-[:MEMBER_OF]->(a:LobbyRegistration)
RETURN DISTINCT r.register_id AS rid, a.register_id AS aid, a.name AS name, coalesce(a.resolved, false) AS resolved"""

Q_NET_CONS = """
MATCH (g:Group {id: $gid})<-[:PART_OF_GROUP]-(:LegalEntity)<-[:COVERS]-(r:LobbyRegistration)-[:HIRED]->(c:LobbyRegistration)
RETURN DISTINCT r.register_id AS rid, c.register_id AS cid, c.name AS name"""

Q_NET_UNITS = """
MATCH (g:Group {id: $gid})<-[:PART_OF_GROUP]-(:LegalEntity)<-[:COVERS]-(r:LobbyRegistration)-[:ATTENDED]->(m:Meeting)-[:HELD_BY]->(u:Unit)
WHERE m.date >= $since
RETURN r.register_id AS rid, u.unit_id AS uid, u.name AS name, u.acronym AS acronym, u.level AS level,
       count(DISTINCT m) AS meetings"""

Q_NET_TOPICS = """
MATCH (g:Group {id: $gid})<-[:PART_OF_GROUP]-(:LegalEntity)<-[:COVERS]-(r:LobbyRegistration)-[:ATTENDED]->(m:Meeting)-[:ON_THEME]->(t:Theme)
WHERE m.date >= $since
RETURN r.register_id AS rid, t.theme_id AS tid, t.name AS name, count(DISTINCT m) AS meetings"""

TOPIC_NOTE = ("Topics are tagged automatically with simple keyword rules on the meeting subject and on the "
              "EU policies each registration declares. Each tag keeps the words it matched, so it can be checked.")


def footer():
    st.divider()
    st.caption("LobbyGraph proof of concept. Every row links to its source. Lobbying is legal and regulated in the EU; "
               "this tool shows declared activity, not wrongdoing.")


def compare_page(since, since_s):
    """All groups side by side: ranking, group vs parent, topic heatmap."""
    rank = pd.DataFrame(run(Q_RANK, since=since_s))
    par = pd.DataFrame(run(Q_PARENT_ONLY, since=since_s))
    df = rank.merge(par, on="gid", how="left").fillna(0)
    df["cost_mid"] = (df["cost_low"] + df["cost_high"]) / 2
    df["cost_text"] = [f"{euro(lo)} – {euro(hi)}" if hi else "–" for lo, hi in zip(df["cost_low"], df["cost_high"])]

    st.title("EU energy lobbying: all groups compared")
    st.caption(f"{len(df)} company groups. Meetings with the European Commission since {since:%d %b %Y}. "
               "Each group = the parent plus every company it consolidates (GLEIF).")
    c1, c2, c3 = st.columns(3)
    c1.metric("Commission meetings", int(df["meetings"].sum()),
              help="Summed per group. A meeting attended by two groups counts for both.")
    c2.metric("Lobby registrations", int(df["registrations"].sum()))
    c3.metric("Lobbyists (FTE)", f"{df['fte'].sum():,.1f}")

    t_rank, t_parent, t_heat = st.tabs(["Ranking", "Group vs parent", "Topics heatmap"])

    with t_rank:
        st.subheader("Who lobbies most in EU energy?")
        metric = st.radio("Rank by", ["Commission meetings", "Declared cost / year", "Lobbyists (FTE)",
                                      "Lobby registrations"], horizontal=True)
        col, label, title = {
            "Commission meetings": ("meetings", "meetings_text", f"Meetings since {since:%d %b %Y}"),
            "Declared cost / year": ("cost_mid", "cost_text", "Declared cost / year (middle of the declared bands, €)"),
            "Lobbyists (FTE)": ("fte", "fte_text", "Lobbyists (full-time equivalent)"),
            "Lobby registrations": ("registrations", "registrations_text", "Registrations covering a group company"),
        }[metric]
        df["meetings_text"] = df["meetings"].astype(int).astype(str)
        df["fte_text"] = df["fte"].map(lambda v: f"{v:,.1f}")
        df["registrations_text"] = df["registrations"].astype(int).astype(str)
        cols = ["group", col, label, "meetings", "registrations", "fte", "cost_text"]
        chart(df[list(dict.fromkeys(cols))], ranking_spec(col, label, title, len(df), float(df[col].max())))
        if metric == "Declared cost / year":
            st.caption("Bars show the middle of each declared cost band; the label shows the full range. "
                       "Where several group companies register, their costs may overlap, so totals are an upper bound.")

    with t_parent:
        st.subheader("What you miss if you only look at the parent company")
        st.caption("Blue: what the parent company's own registration shows. Orange: what the group's other "
                   "registered companies add (for example Enedis and Framatome for EDF, Endesa for Enel).")
        metric = st.radio("Compare", ["Commission meetings", "Lobby registrations", "Lobbyists (FTE)",
                                      "Declared cost / year (upper end)"], horizontal=True)
        g_col, p_col = {"Commission meetings": ("meetings", "p_meetings"),
                        "Lobby registrations": ("registrations", "p_registrations"),
                        "Lobbyists (FTE)": ("fte", "p_fte"),
                        "Declared cost / year (upper end)": ("cost_high", "p_cost_high")}[metric]
        d = df[["group", g_col, p_col]].copy()
        d["rest"] = (d[g_col] - d[p_col]).clip(lower=0)
        d["hidden_pct"] = (d["rest"] / d[g_col]).where(d[g_col] > 0, 0)
        order = d.sort_values(g_col, ascending=False)["group"].tolist()
        long = pd.concat([d.assign(part=PARENT, value=d[p_col], order=0),
                          d.assign(part=REST, value=d["rest"], order=1)])[["group", "part", "value", "order", "hidden_pct"]]
        chart(long, parent_vs_group_spec(order, metric))
        tbl = d.sort_values("hidden_pct", ascending=False).rename(columns={
            "group": "Company group", p_col: "Parent's own registration", g_col: "Whole group",
            "rest": "Added by subsidiaries", "hidden_pct": "Not visible from the parent alone"})
        tbl["Not visible from the parent alone"] = tbl["Not visible from the parent alone"].map(lambda v: f"{v:.0%}")
        num = ["Parent's own registration", "Whole group", "Added by subsidiaries"]
        fmt_num = euro if g_col == "cost_high" else (lambda v: f"{v:,.1f}") if g_col == "fte" else (lambda v: f"{v:,.0f}")
        tbl[num] = tbl[num].apply(lambda c: c.map(fmt_num))
        st.dataframe(tbl, width="stretch", hide_index=True)
        st.caption("A group's meetings are counted once even when several of its companies attend, so "
                   "'Added by subsidiaries' counts only meetings the parent did not attend.")

    with t_heat:
        st.subheader("Which group lobbies on which topic?")
        st.caption(TOPIC_NOTE + " A meeting can have several topics.")
        heat = pd.DataFrame(run(Q_HEAT, since=since_s))
        if heat.empty:
            st.info("No tagged meetings in this period.")
            return
        themes = [t["name"] for t in run(Q_THEMES)]
        grid = pd.MultiIndex.from_product([df["group"], themes], names=["group", "topic"]).to_frame(index=False)
        heat = grid.merge(heat, on=["group", "topic"], how="left").fillna({"meetings": 0})
        heat = heat.merge(df[["group", "meetings"]].rename(columns={"meetings": "total"}), on="group")
        heat["share"] = (heat["meetings"] / heat["total"]).where(heat["total"] > 0, 0)
        mode = st.radio("Show", ["Number of meetings", "Share of the group's meetings"], horizontal=True,
                        help="Share evens out big and small groups: what part of each group's meetings is on this topic.")
        value, fmt = ("meetings", "d") if mode == "Number of meetings" else ("share", ".0%")
        group_order = df.sort_values("meetings", ascending=False)["group"].tolist()
        topic_order = heat.groupby("topic")["meetings"].sum().sort_values(ascending=False).index.tolist()
        chart(heat[["group", "topic", "meetings", "share"]], heatmap_spec(heat, value, fmt, group_order, topic_order))


def network_tab(grp, since, since_s):
    st.subheader(f"{grp['name']}: the whole picture")
    st.caption("Drag to move, scroll to zoom, hover for details. Each line has a source; the other tabs list "
               "every link with its source.")
    a, b, c, d = st.columns([1, 1, 1, 2])
    show_assoc = a.toggle("Associations", value=True)
    show_cons = b.toggle("Consultancies", value=True)
    show_topics = c.toggle("Topics", value=False)
    max_units = d.slider("Commission units to show (most-met first)", 0, 30, 12)
    regs = run(Q_NET_REGS, gid=grp["id"])
    if not regs:
        st.info("No lobby registration covers a company in this group.")
        return
    data = build_network(grp["name"], regs,
                         run(Q_NET_ASSOC, gid=grp["id"]) if show_assoc else [],
                         run(Q_NET_CONS, gid=grp["id"]) if show_cons else [],
                         run(Q_NET_UNITS, gid=grp["id"], since=since_s) if max_units else [],
                         run(Q_NET_TOPICS, gid=grp["id"], since=since_s) if show_topics else [],
                         max_units=max_units)
    components.html(network_html(data, height=620), height=680)
    st.caption(f"Commission units and topics: meetings since {since:%d %b %Y}. A dashed line to an association "
               "means its name was not found in the register.")


# ---------------------------------------------------------------- page
require_login()

try:
    groups = run(Q_GROUPS)
    fresh = run(Q_FRESH)[0]["retrieved"]
except Exception as exc:  # database not reachable or empty
    st.error(f"The graph database is not reachable yet ({type(exc).__name__}). Try again in a minute.")
    st.stop()
if not groups:
    st.warning("The graph is empty. Run the loader first.")
    st.stop()

with st.sidebar:
    st.markdown("## LobbyGraph")
    st.caption("See the whole company behind EU lobbying, with a source for every link.")
    ALL = "All groups (compare)"
    names = {g["name"]: g for g in groups}
    pick = st.radio("Company group", [ALL] + list(names), index=0)
    grp = names.get(pick)
    since = st.date_input("Meetings since", value=date(2024, 12, 1), min_value=date(2014, 12, 1),
                          help="1 Dec 2024 is the start of the current Commission.")
    st.divider()
    st.caption(f"Signed in as {st.session_state['user']}")
    if st.button("Sign out"):
        st.session_state.pop("user", None)
        st.rerun()
    st.caption(f"Data retrieved: {fresh}  \nSources: EU Transparency Register, European Commission meeting "
               "records, GLEIF. A group = the parent and the companies it consolidates (GLEIF).")

since_s = since.isoformat()
if grp is None:
    compare_page(since, since_s)
    footer()
    st.stop()

gid = grp["id"]
ov = run(Q_OVERVIEW, gid=gid)[0]
n_meet = run(Q_MEETING_COUNT, gid=gid, since=since_s)[0]["meetings"]

st.title(grp["name"])
st.caption(f"Ultimate parent LEI [{grp['parent_lei']}](https://search.gleif.org/#/record/{grp['parent_lei']})")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Companies in the group", ov["entities"], help="Parent plus every company it consolidates, per GLEIF.")
c2.metric("Lobby registrations", ov["registrations"], help="Entries in the EU Transparency Register that cover a group company.")
c3.metric("Declared lobbying cost / year",
          f"{euro(ov['cost_low'])} – {euro(ov['cost_high'])}" if ov["cost_high"] else "–",
          help="Sum of the cost bands declared by the group's registrations. May double count: see Data issues.")
c4.metric(f"Commission meetings since {since:%d %b %Y}", n_meet, help="Each meeting counted once, even if two group companies attended.")
if ov["registrations"] > 1:
    st.caption("⚠️ Several group companies register separately. Their declared costs may overlap, so the total is an upper bound.")

tabs = st.tabs(["Network", "Who lobbies", "Meetings", "Topics", "Associations & memberships", "Consultancies",
                "Companies in the group", "Data issues"])

with tabs[0]:
    network_tab(grp, since, since_s)

with tabs[1]:
    st.subheader("Which group companies are registered lobbyists?")
    show(run(Q_REGISTRATIONS, gid=gid), links=["source"], lists=["covers_entity"])

with tabs[2]:
    st.subheader("Meetings with the European Commission")
    rows = run(Q_MEETINGS, gid=gid, since=since_s)
    if rows:
        df = pd.DataFrame(rows)
        left, right = st.columns(2)
        with left:
            st.caption("Meetings per quarter")
            q = pd.to_datetime(df["date"], errors="coerce").dt.to_period("Q").astype(str)
            st.bar_chart(q.value_counts().sort_index())
        with right:
            st.caption("Most-met Commission units")
            st.bar_chart(df["commission_unit"].value_counts().head(10), horizontal=True)
        picked = st.multiselect("Only meetings on these topics", sorted({t for ts in df["topics"] for t in ts}),
                                help=TOPIC_NOTE)
        if picked:
            rows = [r for r in rows if set(r["topics"]) & set(picked)]
    show(rows, links=["source"], lists=["topics", "group_registrations", "officials", "also_present"], height=420)

with tabs[3]:
    st.subheader("What the group lobbies about")
    st.caption(TOPIC_NOTE)
    left, right = st.columns(2)
    with left:
        st.caption(f"Commission meetings per topic since {since:%d %b %Y} (a meeting can have several topics)")
        tm = run(Q_THEME_MEETINGS, gid=gid, since=since_s)
        if tm:
            st.bar_chart(pd.DataFrame(tm).set_index("topic")["meetings"], horizontal=True)
        else:
            st.info("No meetings in this period.")
    with right:
        themes = run(Q_THEMES)
        tnames = {t["name"]: t["id"] for t in themes}
        tpick = st.selectbox("Compare all groups on one topic", list(tnames),
                             index=list(tnames).index("Hydrogen") if "Hydrogen" in tnames else 0)
        show(run(Q_THEME_RANK, tid=tnames[tpick], since=since_s), lists=["declared_by"])
    st.caption("Topics the group's registrations declare in the EU Transparency Register")
    show(run(Q_THEME_DECLARED, gid=gid), links=["source"])

with tabs[4]:
    st.subheader("Associations, think tanks and other organisations the group belongs to")
    st.caption("Association figures are shown next to the group's own, never added to them.")
    show(run(Q_ASSOCIATIONS, gid=gid), links=["source"], lists=["member_via"])

with tabs[5]:
    st.subheader("Consultancies the group hires, and who else hires them")
    show(run(Q_CONSULTANCIES, gid=gid), links=["source"], lists=["other_clients_examples"])

with tabs[6]:
    st.subheader("Companies in the group")
    ents = run(Q_ENTITIES, gid=gid)
    only = st.toggle("Only companies with a lobby registration", value=False)
    if only:
        ents = [e for e in ents if e["registrations"]]
    show(ents, links=["source"], lists=["registrations"], height=480)

with tabs[7]:
    st.subheader("Where sources disagree or data look incomplete")
    show(run(Q_ISSUES, gid=gid))

footer()
