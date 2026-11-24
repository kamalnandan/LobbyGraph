"""LobbyGraph PoC web app: the whole company behind EU lobbying, with a source for every link.
Reads the Neo4j graph built by the loader. All queries run in READ mode."""
import hmac
import os
import re
import time
from datetime import date

import pandas as pd
import streamlit as st
from neo4j import GraphDatabase, READ_ACCESS

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
RETURN m.date AS date, coalesce(u.acronym, u.name) AS commission_unit, m.level AS level, m.subject AS subject,
       group_registrations, collect(DISTINCT o.name) AS officials, collect(DISTINCT other.name) AS also_present, source
ORDER BY date DESC"""

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
    names = {g["name"]: g for g in groups}
    pick = st.radio("Company group", list(names), index=0)
    grp = names[pick]
    since = st.date_input("Meetings since", value=date(2024, 12, 1), min_value=date(2014, 12, 1),
                          help="1 Dec 2024 is the start of the current Commission.")
    st.divider()
    st.caption(f"Signed in as {st.session_state['user']}")
    if st.button("Sign out"):
        st.session_state.pop("user", None)
        st.rerun()
    st.caption(f"Data retrieved: {fresh}  \nSources: EU Transparency Register, European Commission meeting "
               "records, GLEIF. A group = the parent and the companies it consolidates (GLEIF).")

gid, since_s = grp["id"], since.isoformat()
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

tabs = st.tabs(["Who lobbies", "Meetings", "Associations & memberships", "Consultancies", "Companies in the group", "Data issues"])

with tabs[0]:
    st.subheader("Which group companies are registered lobbyists?")
    show(run(Q_REGISTRATIONS, gid=gid), links=["source"], lists=["covers_entity"])

with tabs[1]:
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
    show(rows, links=["source"], lists=["group_registrations", "officials", "also_present"], height=420)

with tabs[2]:
    st.subheader("Associations, think tanks and other organisations the group belongs to")
    st.caption("Association figures are shown next to the group's own, never added to them.")
    show(run(Q_ASSOCIATIONS, gid=gid), links=["source"], lists=["member_via"])

with tabs[3]:
    st.subheader("Consultancies the group hires, and who else hires them")
    show(run(Q_CONSULTANCIES, gid=gid), links=["source"], lists=["other_clients_examples"])

with tabs[4]:
    st.subheader("Companies in the group")
    ents = run(Q_ENTITIES, gid=gid)
    only = st.toggle("Only companies with a lobby registration", value=False)
    if only:
        ents = [e for e in ents if e["registrations"]]
    show(ents, links=["source"], lists=["registrations"], height=480)

with tabs[5]:
    st.subheader("Where sources disagree or data look incomplete")
    show(run(Q_ISSUES, gid=gid))

st.divider()
st.caption("LobbyGraph proof of concept. Every row links to its source. Lobbying is legal and regulated in the EU; "
           "this tool shows declared activity, not wrongdoing.")
