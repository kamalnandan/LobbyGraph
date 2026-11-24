"""In-memory graph with provenance on every node and edge.

Every edge carries the same provenance fields (see the product brief, section 8):
source, source_url, retrieved, valid_from, valid_to, method, confidence, match_method.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict

METHODS = {"official", "derived", "manual"}
CONFIDENCE = {"high", "medium", "low"}


@dataclass
class Provenance:
    source: str
    source_url: str | None = None
    retrieved: str | None = None
    method: str = "official"
    confidence: str = "high"
    match_method: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None

    def __post_init__(self):
        if self.method not in METHODS:
            raise ValueError(f"method must be one of {METHODS}: {self.method}")
        if self.confidence not in CONFIDENCE:
            raise ValueError(f"confidence must be one of {CONFIDENCE}: {self.confidence}")


@dataclass
class Node:
    label: str
    key: str
    props: dict = field(default_factory=dict)


@dataclass
class Edge:
    type: str
    start: tuple[str, str]   # (label, key)
    end: tuple[str, str]
    prov: Provenance
    props: dict = field(default_factory=dict)


class Graph:
    def __init__(self):
        self.nodes: dict[tuple[str, str], Node] = {}
        self.edges: list[Edge] = []

    def add_node(self, label: str, key: str, **props) -> tuple[str, str]:
        k = (label, key)
        if k in self.nodes:
            # merge: keep existing values, fill gaps
            for p, v in props.items():
                if self.nodes[k].props.get(p) in (None, "") and v not in (None, ""):
                    self.nodes[k].props[p] = v
        else:
            self.nodes[k] = Node(label, key, {p: v for p, v in props.items()})
        return k

    def add_edge(self, type_: str, start, end, prov: Provenance, **props):
        for k in (start, end):
            if k not in self.nodes:
                raise KeyError(f"edge {type_} refers to missing node {k}")
        sig = (type_, start, end, tuple(sorted((p, str(v)) for p, v in props.items())))
        if any((e.type, e.start, e.end, tuple(sorted((p, str(v)) for p, v in e.props.items()))) == sig
               for e in self.edges):
            return
        self.edges.append(Edge(type_, start, end, prov, props))

    # helpers used by checks and local answers
    def out(self, k, type_=None):
        return [e for e in self.edges if e.start == k and (type_ is None or e.type == type_)]

    def inc(self, k, type_=None):
        return [e for e in self.edges if e.end == k and (type_ is None or e.type == type_)]

    def by_label(self, label):
        return [n for n in self.nodes.values() if n.label == label]

    def edge_dict(self, e: Edge) -> dict:
        d = {"type": e.type, "start_label": e.start[0], "start_key": e.start[1],
             "end_label": e.end[0], "end_key": e.end[1]}
        d.update(asdict(e.prov))
        d.update(e.props)
        return d
