"""Layer 2 container: directed multigraph of symbols with strong/weak edges."""
from __future__ import annotations

import networkx as nx

from .models import Edge, EdgeStrength, Symbol


class CodeGraph:
    """Wraps a networkx MultiDiGraph. Nodes are symbol ids (plus pseudo-nodes
    like 'file:config.yaml' for edges sourced from non-code files)."""

    def __init__(self) -> None:
        self.g = nx.MultiDiGraph()
        self.edges: list[Edge] = []

    def add_symbol(self, sym: Symbol) -> None:
        self.g.add_node(sym.id, symbol=sym)

    def add_edge(self, edge: Edge) -> None:
        # Self-references (recursion, a class referring to itself) are not
        # evidence of external use — drop them at insertion time.
        if edge.src == edge.dst:
            return
        self.edges.append(edge)
        self.g.add_edge(edge.src, edge.dst, edge=edge)

    def inbound(self, symbol_id: str) -> list[Edge]:
        """Inbound edges, excluding edges whose source is nested inside the
        target (a method referencing its own class proves nothing)."""
        if symbol_id not in self.g:
            return []
        result = []
        for src, _dst, data in self.g.in_edges(symbol_id, data=True):
            if src.startswith(symbol_id + ".") or src.startswith(symbol_id + ":"):
                continue
            result.append(data["edge"])
        return result

    def inbound_split(self, symbol_id: str) -> tuple[list[Edge], list[Edge]]:
        strong, weak = [], []
        for e in self.inbound(symbol_id):
            (strong if e.strength is EdgeStrength.STRONG else weak).append(e)
        return strong, weak
