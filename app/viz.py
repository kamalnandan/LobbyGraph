"""Pictures for the LobbyGraph app: Vega-Lite chart specs and the interactive network.

Pure functions (data in, spec or HTML out), so they can be tested without Streamlit or Neo4j.
"""
from __future__ import annotations
import json
import re

import pandas as pd

BLUE, ORANGE, GREY = "#1f5f99", "#e07b39", "#9aa5b1"
PARENT, REST = "Parent's own registration", "Rest of the group (subsidiaries)"


# ---------------------------------------------------------------- charts (Vega-Lite)
def heatmap_spec(df: pd.DataFrame, value: str, fmt: str, group_order: list, topic_order: list) -> dict:
    """Groups x topics grid. df columns: group, topic, meetings, share. value = 'meetings' or 'share'."""
    vmax = float(df[value].max() or 1)
    x = {"field": "topic", "type": "nominal", "sort": topic_order, "title": None,
         "axis": {"labelAngle": -35, "labelLimit": 220, "orient": "top"}}
    y = {"field": "group", "type": "nominal", "sort": group_order, "title": None}
    tip = [{"field": "group", "title": "Group"}, {"field": "topic", "title": "Topic"},
           {"field": "meetings", "type": "quantitative", "title": "Meetings"},
           {"field": "share", "type": "quantitative", "title": "Share of the group's meetings", "format": ".0%"}]
    return {
        "height": 38 * len(group_order),
        "layer": [
            {"mark": {"type": "rect", "stroke": "white", "strokeWidth": 2},
             "encoding": {"x": x, "y": y, "tooltip": tip,
                          "color": {"field": value, "type": "quantitative", "legend": None,
                                    "scale": {"scheme": "blues", "domainMin": 0}}}},
            {"mark": {"type": "text", "fontSize": 12},
             "transform": [{"filter": f"datum.{value} > 0"}],
             "encoding": {"x": x, "y": y, "tooltip": tip,
                          "text": {"field": value, "type": "quantitative", "format": fmt},
                          "color": {"condition": {"test": f"datum.{value} > {vmax * 0.55}", "value": "white"},
                                    "value": "#1a1a1a"}}},
        ],
    }


def ranking_spec(value: str, label: str, title: str, n: int, vmax: float) -> dict:
    """Horizontal bars, biggest first, with a text label at the end of each bar.
    Data columns: group, <value>, <label>, meetings, registrations, fte, cost_text."""
    tip = [{"field": "group", "title": "Group"},
           {"field": "meetings", "type": "quantitative", "title": "Meetings"},
           {"field": "cost_text", "title": "Declared cost / year"},
           {"field": "fte", "type": "quantitative", "title": "Lobbyists (FTE)", "format": ".1f"},
           {"field": "registrations", "type": "quantitative", "title": "Registrations"}]
    return {
        "height": 34 * n,
        "encoding": {
            "y": {"field": "group", "type": "nominal", "sort": "-x", "title": None},
            "x": {"field": value, "type": "quantitative", "title": title,
                  "scale": {"domain": [0, (vmax or 1) * 1.25], "nice": False}},
            "tooltip": tip,
        },
        "layer": [
            {"mark": {"type": "bar", "color": BLUE, "cornerRadiusEnd": 3}},
            {"mark": {"type": "text", "align": "left", "dx": 4, "fontSize": 12},
             "encoding": {"text": {"field": label, "type": "nominal"}}},
        ],
    }


def parent_vs_group_spec(group_order: list, title: str) -> dict:
    """Stacked bars: the parent's own registration vs the rest of the group.
    Data columns (long format): group, part, value, order, total, hidden_pct."""
    return {
        "height": 34 * len(group_order),
        "mark": {"type": "bar"},
        "encoding": {
            "y": {"field": "group", "type": "nominal", "sort": group_order, "title": None},
            "x": {"field": "value", "type": "quantitative", "stack": "zero", "title": title},
            "color": {"field": "part", "type": "nominal", "title": None,
                      "scale": {"domain": [PARENT, REST], "range": [BLUE, ORANGE]},
                      "legend": {"orient": "top"}},
            "order": {"field": "order", "type": "quantitative"},
            "tooltip": [{"field": "group", "title": "Group"}, {"field": "part", "title": "Part"},
                        {"field": "value", "type": "quantitative", "title": title, "format": ",.4~f"},
                        {"field": "hidden_pct", "type": "quantitative",
                         "title": "Not visible from the parent alone", "format": ".0%"}],
        },
    }


# ---------------------------------------------------------------- network (vis-network)
NODE_STYLE = {
    "group": {"shape": "box", "color": {"background": "#14365c", "border": "#14365c"},
              "font": {"color": "white", "size": 16, "bold": True}, "margin": 10},
    "registration": {"shape": "dot", "color": {"background": BLUE, "border": "#14365c"}},
    "association": {"shape": "dot", "color": {"background": ORANGE, "border": "#a9541f"}, "size": 10},
    "consultancy": {"shape": "diamond", "color": {"background": "#7b4fa0", "border": "#553570"}, "size": 11},
    "unit": {"shape": "square", "color": {"background": "#2e8b57", "border": "#1d5c39"}},
    "topic": {"shape": "triangle", "color": {"background": "#17a2b8", "border": "#0f6f7e"}, "size": 12},
}
LEGEND = [("group", "Company group"), ("registration", "Lobby registration (size = lobbyists)"),
          ("association", "Association / membership"), ("consultancy", "Consultancy hired"),
          ("unit", "Commission unit met (line width = meetings)"), ("topic", "Meeting topic")]
NO_DG_PREFIX = {"SG", "SJ", "JRC", "IDEA", "UKTF"}


def unit_label(name: str, acronym, level) -> str:
    """'Cabinet of Commissioner Dan Jørgensen' -> 'Cab. Dan Jørgensen'; DG with acronym ENER -> 'DG ENER'."""
    if isinstance(acronym, str) and acronym:
        return acronym if acronym in NO_DG_PREFIX else "DG " + acronym
    m = re.match(r"Cabinet of (?:the )?(?:Executive Vice-President|High Representative[^ ]*|President|Commissioner)\s+(.*)",
                 name or "")
    return "Cab. " + m.group(1) if m else (name or "?")


def _short(text: str, n: int = 32) -> str:
    text = text or "?"
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def build_network(group: str, regs: list, assoc: list, cons: list, units: list, topics: list,
                  max_units: int = 15) -> dict:
    """Nodes and edges for one group. Each list holds rows from the app's network queries."""
    nodes, edges, seen = [], [], set()

    def node(nid, kind, label, title, **kw):
        if nid in seen:
            return
        seen.add(nid)
        nodes.append({"id": nid, "label": _short(label), "title": title, "kind": kind, **NODE_STYLE[kind], **kw})

    def edge(a, b, title, **kw):
        edges.append({"from": a, "to": b, "title": title, **kw})

    node("g", "group", group, f"{group}\nCompany group (parent and the companies it consolidates, per GLEIF)")
    for r in regs:
        fte = float(r.get("fte") or 0)
        node("r:" + r["rid"], "registration", r["name"],
             f"{r['name']}\nRegister ID {r['rid']}\nLobbyists (FTE): {fte:g}\nCovers: {', '.join(r['entities'])}",
             size=12 + min(fte, 30))
        edge("g", "r:" + r["rid"], "Group company with its own lobby registration\nSource: GLEIF + EU Transparency Register",
             width=2, color={"color": "#14365c"})
    for a in assoc:
        found = "" if a.get("resolved") else "\n(name not found in the register)"
        node("a:" + a["aid"], "association", a["name"], f"{a['name']}\nAssociation or membership{found}")
        edge("r:" + a["rid"], "a:" + a["aid"], "Member of\nSource: EU Transparency Register", dashes=not a.get("resolved"))
    for c in cons:
        node("c:" + c["cid"], "consultancy", c["name"], f"{c['name']}\nConsultancy hired")
        edge("r:" + c["rid"], "c:" + c["cid"], "Hired\nSource: EU Transparency Register")
    if units:
        tot = pd.DataFrame(units).groupby("uid")["meetings"].sum().sort_values(ascending=False)
        keep = set(tot.index[:max_units])
        for u in units:
            if u["uid"] not in keep:
                continue
            node("u:" + u["uid"], "unit", unit_label(u["name"], u.get("acronym"), u.get("level")),
                 f"{u['name']}\n{int(tot[u['uid']])} meetings with this group", size=10 + min(int(tot[u["uid"]]), 25))
            edge("r:" + u["rid"], "u:" + u["uid"], f"{u['meetings']} meetings\nSource: European Commission meeting records",
                 width=1 + min(u["meetings"], 12) / 2, label=str(u["meetings"]), color={"color": "#8fbc9f"})
    for t in topics:
        node("t:" + t["tid"], "topic", t["name"], f"{t['name']}\nTopic (keyword rules on meeting subjects)")
        edge("r:" + t["rid"], "t:" + t["tid"], f"{t['meetings']} meetings on {t['name']}",
             width=1 + min(t["meetings"], 12) / 2, label=str(t["meetings"]), color={"color": "#8fd0da"}, dashes=True)
    return {"nodes": nodes, "edges": edges}


_HTML = """<!doctype html><html><head><meta charset="utf-8">
<script src="https://unpkg.com/vis-network@9.1/standalone/umd/vis-network.min.js"></script>
<style>
 body{margin:0;font-family:"Source Sans Pro",sans-serif;font-size:13px;color:#31333f}
 #legend{display:flex;flex-wrap:wrap;gap:14px;margin:4px 2px 8px}
 #legend span{display:inline-flex;align-items:center;gap:5px}
 #legend i{display:inline-block;width:11px;height:11px;border-radius:50%}
 #net{height:__H__px;border:1px solid #e6e9ef;border-radius:8px}
 .tip{white-space:pre-line;max-width:340px}
</style></head><body>
<div id="legend">__LEGEND__</div><div id="net"></div>
<script>
const data = __DATA__;
const tip = t => { const d = document.createElement('div'); d.className = 'tip'; d.textContent = t; return d; };
data.nodes.forEach(n => { n.title = tip(n.title); });
data.edges.forEach(e => { e.title = tip(e.title); });
const net = new vis.Network(document.getElementById('net'),
  {nodes: new vis.DataSet(data.nodes), edges: new vis.DataSet(data.edges)},
  {physics: {solver: 'forceAtlas2Based', forceAtlas2Based: {gravitationalConstant: -70, springLength: 120},
             stabilization: {iterations: 300}},
   interaction: {hover: true, tooltipDelay: 100, navigationButtons: true, keyboard: false},
   nodes: {font: {size: 13}, borderWidth: 1},
   edges: {color: {color: '#b8c2cc', highlight: '#333', hover: '#555'}, smooth: {type: 'continuous'},
           font: {size: 10, align: 'middle', strokeWidth: 3}}});
net.once('stabilizationIterationsDone', () => net.setOptions({physics: false}));
</script></body></html>"""


def network_html(data: dict, height: int = 620) -> str:
    """Self-contained HTML page with the interactive network (vis-network from a CDN)."""
    kinds = {n["kind"] for n in data["nodes"]}
    legend = "".join(f'<span><i style="background:{NODE_STYLE[k]["color"]["background"]}"></i>{text}</span>'
                     for k, text in LEGEND if k in kinds)
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return _HTML.replace("__H__", str(height)).replace("__LEGEND__", legend).replace("__DATA__", blob)
