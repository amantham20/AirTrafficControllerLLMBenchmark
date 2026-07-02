"""Airport graph: nodes, edges, runways, routing and geometry queries.

The airport is an undirected graph. Coordinates are meters in a local
east/north frame; edge lengths default to euclidean distance between
endpoints so geometry and physics stay consistent.
"""

from __future__ import annotations

import heapq
import json
import math
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from .models import EdgeType, NodeType, Position, KT_TO_MS

DATA_DIR = Path(__file__).parent / "data"


class Node(BaseModel):
    id: str
    type: NodeType
    x: float
    y: float
    # For hold_short nodes: the runway this hold-short line protects.
    hold_short_for: Optional[str] = None


class Edge(BaseModel):
    a: str
    b: str
    type: EdgeType
    name: str
    length_m: float
    speed_limit_ms: float

    def other(self, node_id: str) -> str:
        return self.b if node_id == self.a else self.a

    @property
    def key(self) -> tuple[str, str]:
        return tuple(sorted((self.a, self.b)))  # type: ignore[return-value]


class Runway(BaseModel):
    id: str                    # e.g. "05/23"
    ends: dict[str, str]       # end name -> threshold node id
    headings: dict[str, float]
    nodes: list[str]           # ordered from first end's threshold to second's
    length_m: float = 0.0
    # Every node that is part of this runway's protected area.
    node_set: set[str] = Field(default_factory=set)

    def end_names(self) -> list[str]:
        return list(self.ends.keys())

    def threshold(self, end: str) -> str:
        return self.ends[end]

    def opposite_end(self, end: str) -> str:
        names = self.end_names()
        return names[1] if end == names[0] else names[0]


class Airport:
    def __init__(self, raw: dict):
        self.id: str = raw["id"]
        self.name: str = raw.get("name", self.id)
        self.description: str = raw.get("description", "")
        self.nodes: dict[str, Node] = {}
        self.edges: dict[tuple[str, str], Edge] = {}
        self.adjacency: dict[str, list[Edge]] = {}
        self.runways: dict[str, Runway] = {}

        limits = raw.get("speed_limits_kt", {})
        default_limits = {"ramp": 10.0, "taxiway": 20.0, "runway": 30.0}

        for n in raw["nodes"]:
            node = Node(**n)
            self.nodes[node.id] = node
            self.adjacency[node.id] = []

        for e in raw["edges"]:
            na, nb = self.nodes[e["a"]], self.nodes[e["b"]]
            length = e.get("length_m") or math.dist((na.x, na.y), (nb.x, nb.y))
            etype = EdgeType(e["type"])
            limit_kt = e.get("speed_limit_kt") or limits.get(
                etype.value, default_limits[etype.value])
            edge = Edge(
                a=e["a"], b=e["b"], type=etype, name=e["name"],
                length_m=length, speed_limit_ms=limit_kt * KT_TO_MS)
            self.edges[edge.key] = edge
            self.adjacency[edge.a].append(edge)
            self.adjacency[edge.b].append(edge)

        for r in raw["runways"]:
            rw = Runway(**r)
            rw.node_set = set(rw.nodes)
            rw.length_m = sum(
                self.edge_between(a, b).length_m
                for a, b in zip(rw.nodes, rw.nodes[1:]))
            self.runways[rw.id] = rw

        self.gates: list[str] = sorted(
            n.id for n in self.nodes.values() if n.type == NodeType.GATE)

    # -- basic lookups ------------------------------------------------------

    @classmethod
    def load(cls, airport_id: str = "kmbs") -> "Airport":
        path = DATA_DIR / f"{airport_id.lower()}.json"
        with open(path) as f:
            return cls(json.load(f))

    def node(self, node_id: str) -> Node:
        return self.nodes[node_id]

    def edge_between(self, a: str, b: str) -> Optional[Edge]:
        return self.edges.get(tuple(sorted((a, b))))  # type: ignore[arg-type]

    def neighbors(self, node_id: str) -> list[str]:
        return [e.other(node_id) for e in self.adjacency[node_id]]

    def runway_for_end(self, end: str) -> Optional[Runway]:
        """Look up a runway by end name, e.g. '05' -> runway 05/23."""
        for rw in self.runways.values():
            if end in rw.ends:
                return rw
        return None

    # -- geometry -----------------------------------------------------------

    def xy_of(self, pos: Position) -> tuple[float, float]:
        """Interpolated ground coordinates for a position."""
        if pos.node is not None:
            n = self.nodes[pos.node]
            return (n.x, n.y)
        assert pos.edge_a and pos.edge_b
        na, nb = self.nodes[pos.edge_a], self.nodes[pos.edge_b]
        edge = self.edge_between(pos.edge_a, pos.edge_b)
        frac = 0.0 if edge is None or edge.length_m == 0 else min(
            1.0, pos.dist_m / edge.length_m)
        return (na.x + (nb.x - na.x) * frac, na.y + (nb.y - na.y) * frac)

    def heading_between(self, a: str, b: str) -> float:
        na, nb = self.nodes[a], self.nodes[b]
        return math.degrees(math.atan2(nb.x - na.x, nb.y - na.y)) % 360.0

    def ground_distance(self, p1: Position, p2: Position) -> float:
        x1, y1 = self.xy_of(p1)
        x2, y2 = self.xy_of(p2)
        return math.dist((x1, y1), (x2, y2))

    # -- runway protection --------------------------------------------------

    def runways_touching_position(self, pos: Position) -> set[str]:
        """Runway ids whose protected area this ground position occupies.

        A position occupies a runway if it is at one of the runway's nodes,
        or anywhere on an edge that has an endpoint in the runway's node set
        (entry stubs, exit stubs and crossing segments inside the hold-short
        lines are all protected surface).
        """
        occupied: set[str] = set()
        touched: list[str] = []
        if pos.node is not None:
            touched = [pos.node]
        elif pos.edge_a is not None and pos.edge_b is not None:
            touched = [pos.edge_a, pos.edge_b]
        for rw in self.runways.values():
            if any(t in rw.node_set for t in touched):
                occupied.add(rw.id)
        return occupied

    def hold_short_runway(self, node_id: str) -> Optional[str]:
        n = self.nodes.get(node_id)
        return n.hold_short_for if n is not None else None

    def runway_exits(self, end: str) -> list[tuple[float, str, str]]:
        """Exits usable when landing on `end`, ordered by rollout distance.

        Returns (distance_from_threshold_m, runway_node, off_runway_node).
        An exit is any non-runway edge attached to a runway node; the
        landing threshold's own entry stub is excluded (you can't exit
        backwards at touchdown).
        """
        rw = self.runway_for_end(end)
        assert rw is not None
        ordered = rw.nodes if rw.nodes[0] == rw.threshold(end) else list(
            reversed(rw.nodes))
        exits: list[tuple[float, str, str]] = []
        dist = 0.0
        for i, node_id in enumerate(ordered):
            if i > 0:
                dist += self.edge_between(ordered[i - 1], node_id).length_m
                for e in self.adjacency[node_id]:
                    if e.type != EdgeType.RUNWAY:
                        exits.append((dist, node_id, e.other(node_id)))
        return exits

    # -- routing ------------------------------------------------------------

    def route_edges(self, route: list[str]) -> Optional[list[Edge]]:
        """Edges along a node route, or None if any hop is not an edge."""
        edges = []
        for a, b in zip(route, route[1:]):
            e = self.edge_between(a, b)
            if e is None:
                return None
            edges.append(e)
        return edges

    def shortest_path(
        self,
        start: str,
        goal: str,
        avoid_runway_edges: bool = True,
        crossing_penalty_m: float = 200.0,
        closed_runways: Optional[set[str]] = None,
    ) -> Optional[list[str]]:
        """Dijkstra over edge lengths. Runway edges are avoided by default
        (taxi routes should not travel *along* runways); crossing a runway
        (passing through one of its nodes) costs a small penalty so
        non-crossing routes win ties."""
        if start == goal:
            return [start]
        runway_nodes: dict[str, set[str]] = {
            rid: rw.node_set for rid, rw in self.runways.items()}
        dist: dict[str, float] = {start: 0.0}
        prev: dict[str, str] = {}
        pq: list[tuple[float, str]] = [(0.0, start)]
        while pq:
            d, u = heapq.heappop(pq)
            if u == goal:
                break
            if d > dist.get(u, math.inf):
                continue
            for e in self.adjacency[u]:
                if avoid_runway_edges and e.type == EdgeType.RUNWAY:
                    continue
                v = e.other(u)
                w = e.length_m
                for rid, nodes in runway_nodes.items():
                    if v in nodes:
                        w += crossing_penalty_m
                        if closed_runways and rid in closed_runways:
                            w += 1e7  # effectively closed
                nd = d + w
                if nd < dist.get(v, math.inf):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))
        if goal not in dist:
            return None
        path = [goal]
        while path[-1] != start:
            path.append(prev[path[-1]])
        return list(reversed(path))

    def route_length_m(self, route: list[str]) -> float:
        edges = self.route_edges(route)
        if edges is None:
            raise ValueError(f"route is not connected: {route}")
        return sum(e.length_m for e in edges)

    def min_taxi_time_s(self, route: list[str], taxi_speed_ms: float) -> float:
        """Theoretical unimpeded taxi time along a route: each edge at the
        lesser of the aircraft's taxi speed and the edge speed limit."""
        edges = self.route_edges(route)
        if edges is None:
            raise ValueError(f"route is not connected: {route}")
        return sum(
            e.length_m / min(taxi_speed_ms, e.speed_limit_ms) for e in edges)

    # -- serialization for UI / LLM ----------------------------------------

    def to_public_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "nodes": [n.model_dump() for n in self.nodes.values()],
            "edges": [
                {"a": e.a, "b": e.b, "type": e.type.value, "name": e.name,
                 "length_m": round(e.length_m, 1)}
                for e in self.edges.values()
            ],
            "runways": [
                {"id": r.id, "ends": r.ends, "headings": r.headings,
                 "nodes": r.nodes, "length_m": round(r.length_m, 1)}
                for r in self.runways.values()
            ],
            "gates": self.gates,
        }
