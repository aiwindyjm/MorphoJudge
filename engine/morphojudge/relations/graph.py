"""REL-001: SoftwareMap graph with forward/backward adjacency and budgeted
traversal. Graph queries never touch the filesystem and never invoke tools;
they only read the in-memory map. Cycles terminate via visited sets.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..contracts.domain import SoftwareMap

DEFAULT_MAX_DEPTH = 10
DEFAULT_MAX_NODES = 1000


@dataclass
class WalkResult:
    node_ids: set[str] = field(default_factory=set)
    edge_ids: set[str] = field(default_factory=set)
    truncated: bool = False
    stop_reason: str | None = None


@dataclass
class SoftwareGraph:
    software_map: SoftwareMap
    forward: dict[str, list[tuple[str, str]]] = field(default_factory=dict)   # src -> [(edge_id, dst)]
    backward: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # dst -> [(edge_id, src)]
    dangling_edges: list[str] = field(default_factory=list)                   # edge ids with missing endpoints

    def walk(
        self,
        start: str | set[str],
        *,
        direction: str,
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_nodes: int = DEFAULT_MAX_NODES,
    ) -> WalkResult:
        if direction not in ("up", "down"):
            raise ValueError("direction must be 'up' or 'down'")
        adjacency = self.forward if direction == "down" else self.backward
        starts = {start} if isinstance(start, str) else set(start)
        result = WalkResult()

        queue: list[tuple[str, int]] = []
        for node_id in sorted(starts):
            if node_id in self.forward or node_id in self.backward or node_id in self._all_nodes:
                result.node_ids.add(node_id)
                queue.append((node_id, 0))
            else:
                result.node_ids.add(node_id)  # 起点（可能孤立）仍然返回
        if not queue:
            return result

        visited: set[str] = set(result.node_ids)
        head = 0
        pending_deeper = False
        while head < len(queue):
            node_id, depth = queue[head]
            head += 1
            if depth >= max_depth:
                if adjacency.get(node_id):
                    pending_deeper = True
                continue
            for edge_id, neighbor in adjacency.get(node_id, []):
                result.edge_ids.add(edge_id)
                if neighbor in visited:
                    continue
                if len(result.node_ids) >= max_nodes:
                    result.truncated = True
                    result.stop_reason = "max_nodes"
                    return result
                visited.add(neighbor)
                result.node_ids.add(neighbor)
                queue.append((neighbor, depth + 1))
        if pending_deeper:
            result.truncated = True
            result.stop_reason = "max_depth"
        return result

    def downstream(self, start: str | set[str], **kwargs) -> WalkResult:
        return self.walk(start, direction="down", **kwargs)

    def upstream(self, start: str | set[str], **kwargs) -> WalkResult:
        return self.walk(start, direction="up", **kwargs)

    @property
    def _all_nodes(self) -> set[str]:
        return {node.id for node in self.software_map.nodes}


def build_graph(software_map: SoftwareMap) -> SoftwareGraph:
    """Validate edge endpoints; dangling edges are recorded, never silently
    dropped from the map, and excluded from adjacency."""

    graph = SoftwareGraph(software_map=software_map)
    node_ids = graph._all_nodes
    for edge in software_map.edges:
        if edge.source_id not in node_ids or edge.target_id not in node_ids:
            graph.dangling_edges.append(edge.id)
            continue
        graph.forward.setdefault(edge.source_id, []).append((edge.id, edge.target_id))
        graph.backward.setdefault(edge.target_id, []).append((edge.id, edge.source_id))
    return graph
