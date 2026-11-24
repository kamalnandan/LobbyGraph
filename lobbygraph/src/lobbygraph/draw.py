"""Draws a readable overview of the core graph for the covered groups (PNG)."""
from __future__ import annotations
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

from .answers import group_registrations

NAV, GREY = "#1F3A5F", "#4A5866"
FILL = {"Group": "#1F3A5F", "LegalEntity": "#E8EEF5", "LobbyRegistration": "#FFF4D6", "assoc": "#E6F2EA",
        "consult": "#F6E7F2", "Meeting": "#EEF0F2"}
ECOL = {"PART_OF_GROUP": "#1F3A5F", "COVERS": "#B7791F", "EXCLUDES": "#C0392B", "MEMBER_OF": "#2F855A",
        "HIRED": "#97266D", "ATTENDED": "#718096", "HOLDS_STAKE": "#C0392B"}


def draw(g, path, title):
    fig, ax = plt.subplots(figsize=(16, 10.5), dpi=170)
    ax.set_xlim(0, 160); ax.set_ylim(-5, 110); ax.axis("off")
    pos = {}

    def box(k, x, y, text, fill, w=24, h=4.2, fc="black", fs=7.6, bold=False):
        ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h, boxstyle="round,pad=0.25,rounding_size=0.8",
                                    fc=fill, ec=NAV, lw=0.9, zorder=3))
        ax.text(x, y, text, ha="center", va="center", fontsize=fs, color=fc, zorder=4,
                fontweight="bold" if bold else "normal")
        pos[k] = (x, y, w)

    def line(a, b, t, style="-", label=None):
        (x1, y1, w1), (x2, y2, w2) = pos[a], pos[b]
        xa = x1 + w1 / 2 if x2 > x1 else x1 - w1 / 2
        xb = x2 - w2 / 2 if x2 > x1 else x2 + w2 / 2
        if abs(x2 - x1) < 1:
            xa = xb = x1; ya, yb = y1 - 2.1 * (1 if y2 < y1 else -1), y2 + 2.1 * (1 if y2 < y1 else -1)
            ax.annotate("", (xb, yb), (xa, ya), arrowprops=dict(arrowstyle="-|>", color=ECOL[t], lw=1.2, ls=style), zorder=2)
            if label:
                ax.text(xa + 1, (ya + yb) / 2, label, fontsize=6.6, color=ECOL[t], va="center", fontweight="bold")
            return
        ax.annotate("", (xb, y2), (xa, y1), arrowprops=dict(arrowstyle="-|>", color=ECOL[t], lw=0.9, ls=style,
                    alpha=0.85, shrinkA=0, shrinkB=0), zorder=2)

    groups = [("G1", 78), ("G2", 22)]
    reg_y = {}
    for gid, gy in groups:
        gk = ("Group", gid)
        ents, regs = group_registrations(g, gid)
        box(gk, 12, gy, g.nodes[gk].props["name"] + " group", FILL["Group"], w=20, h=5, fc="white", fs=9, bold=True)
        ents = sorted(ents, key=lambda e: (e[1] != g.nodes[gk].props["parent_lei"], g.nodes[e].props["name"]))
        n = len(ents)
        for i, e in enumerate(ents):
            y = gy + (n - 1) * 2.6 - i * 5.2
            p = g.nodes[e].props
            box(e, 42, y, f"{p['name'][:30]} ({p['country']})", FILL["LegalEntity"], w=27)
            line(e, gk, "PART_OF_GROUP")
        for j, r in enumerate(sorted(regs, key=lambda r: g.nodes[r].props["name"])):
            y = gy + 9 - j * 12 if len(regs) > 1 else gy
            p = g.nodes[r].props
            box(r, 76, y, f"{p['name']}\nregister {r[1]}\n€{p['cost_band']}, {p['fte']} FTE", FILL["LobbyRegistration"],
                w=26, h=7.2, fs=7.2)
            reg_y[r] = y
            for e in g.out(r, "COVERS"):
                line(r, e.end, "COVERS", "-" if e.prov.method == "manual" else "--")
    for e in g.edges:
        if e.type == "EXCLUDES" and e.start in pos and e.end in pos:
            line(e.start, e.end, "EXCLUDES", ":")
    for e in g.edges:
        if e.type == "HOLDS_STAKE":
            (x1, y1, w), (x2, y2, _) = pos[e.start], pos[e.end]
            ax.annotate("", (x2 - w / 2 + 2, y2 + 2.1), (x1 - w / 2 + 2, y1 - 2.1),
                        arrowprops=dict(arrowstyle="-|>", color=ECOL["HOLDS_STAKE"], lw=1.3, ls="--",
                                        connectionstyle="arc3,rad=0.25"), zorder=2)
            ax.text(21, (y1 + y2) / 2, "HOLDS_STAKE\n(no control)", fontsize=7, color=ECOL["HOLDS_STAKE"],
                    fontweight="bold", ha="center")

    regs_all = list(reg_y)
    # associations shared by both groups, plus consultancies, plus meetings
    by_assoc = {}
    for r in regs_all:
        for e in g.out(r, "MEMBER_OF"):
            if g.nodes[e.end].props.get("resolved"):
                by_assoc.setdefault(e.end, set()).add(r)
    g1 = set(group_registrations(g, "G1")[1]); g2 = set(group_registrations(g, "G2")[1])
    shared = [a for a, rs in by_assoc.items() if rs & g1 and rs & g2]
    shared.sort(key=lambda a: g.nodes[a].props["name"])
    for i, a in enumerate(shared):
        p = g.nodes[a].props
        label = p["name"] if len(p["name"]) <= 34 else (p.get("acronym") or p["name"][:31] + "...")
        box(a, 120, 92 - i * 5.2, label, FILL["assoc"], w=32)
        for r in by_assoc[a]:
            line(r, a, "MEMBER_OF")
    others = len(by_assoc) - len(shared)
    ax.text(120, 92 - len(shared) * 5.2 + 0.5, f"+ {others} more associations (one group only)", ha="center",
            fontsize=7.2, color=ECOL["MEMBER_OF"], style="italic")
    consult = {}
    for r in regs_all:
        for e in g.out(r, "HIRED"):
            consult.setdefault(e.end, []).append(r)
    cy0 = 92 - len(shared) * 5.2 - 8
    for i, c in enumerate(sorted(consult, key=lambda c: g.nodes[c].props["name"])):
        n_clients = len({e.start for e in g.inc(c, "HIRED")})
        box(c, 120, cy0 - i * 5.6, f"{g.nodes[c].props['name']} ({n_clients} clients)", FILL["consult"], w=32)
        for r in consult[c]:
            line(r, c, "HIRED")
    # meetings: top Commission units met, counted once per meeting
    per_unit = {}
    for r in regs_all:
        for e in g.out(r, "ATTENDED"):
            p = g.nodes[e.end].props
            u = p.get("unit_acronym") or p["unit"].replace("Cabinet of ", "Cab. ").replace("Executive Vice-President", "EVP").replace("Commissioner ", "")
            per_unit.setdefault(u, {}).setdefault(r, set()).add(e.end)
    top = sorted(per_unit, key=lambda u: -len(set().union(*per_unit[u].values())))[:7]
    my0 = cy0 - len(consult) * 5.6 - 6
    total = len({e.end for r in regs_all for e in g.out(r, "ATTENDED")})
    for i, u in enumerate(top):
        n = len(set().union(*per_unit[u].values()))
        k = ("UnitBox", u)
        box(k, 120, my0 - i * 4.6, f"{u[:34]}: {n} meetings", FILL["Meeting"], w=32, h=3.8, fs=7)
        for r in per_unit[u]:
            line(r, k, "ATTENDED")
    ax.text(120, my0 + 3.2, f"Top Commission units met ({total} meetings in total, 2014-2026)",
            ha="center", fontsize=7.2, color=ECOL["ATTENDED"], style="italic")
    ax.text(2, 107.5, title, fontsize=15, fontweight="bold", color=NAV)
    ax.text(2, 104, "Pipeline output, 2 November 2026. Associations shown: those both groups belong to. Meetings: Commission cabinets (2024-2029) and Directors-General.",
            fontsize=8.5, color=GREY)
    for i, (t, c) in enumerate(ECOL.items()):
        ax.plot([2 + i * 20, 6 + i * 20], [-3, -3], color=c, lw=2)
        ax.text(7 + i * 20, -3, t, fontsize=7, va="center", color=c, fontweight="bold")
    fig.savefig(path, bbox_inches="tight", pad_inches=0.1, facecolor="white")
