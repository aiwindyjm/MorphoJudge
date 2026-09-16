"""REL-001: graph adjacency, cycles, budgets, dangling edges."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from morphojudge.contracts.domain import (
    MapEdge,
    MapNode,
    MapRelation,
    Resolution,
    SoftwareMap,
)
from morphojudge.relations.graph import build_graph

SNAP = "a" * 64


def node(node_id: str, kind: str = "method") -> MapNode:
    return MapNode(
        id=node_id,
        snapshot_id=SNAP,
        kind=kind,  # type: ignore[arg-type]
        label=node_id,
        resolution=Resolution.RESOLVED,
    )


def edge(source: str, target: str, relation: str = "calls", resolution: str = "resolved", suffix: str = "") -> MapEdge:
    return MapEdge(
        id=f"edge:{source}->{target}{suffix}",
        snapshot_id=SNAP,
        relation=MapRelation(relation),  # type: ignore[arg-type]
        source_id=source,
        target_id=target,
        resolution=Resolution(resolution),  # type: ignore[arg-type]
    )


def test_shared_method_reaches_two_pages_upstream():
    software_map = SoftwareMap(
        snapshot_id=SNAP,
        nodes=[node("page:a", "page"), node("page:b", "page"), node("fa"), node("fb"), node("shared")],
        edges=[edge("page:a", "fa"), edge("page:b", "fb"), edge("fa", "shared"), edge("fb", "shared")],
    )
    graph = build_graph(software_map)
    result = graph.upstream("shared")
    assert {"page:a", "page:b", "fa", "fb", "shared"} <= result.node_ids
    assert not result.truncated


def test_call_cycle_terminates():
    software_map = SoftwareMap(
        snapshot_id=SNAP,
        nodes=[node("a"), node("b"), node("c")],
        edges=[edge("a", "b"), edge("b", "c"), edge("c", "a")],
    )
    graph = build_graph(software_map)
    result = graph.downstream("a")
    assert result.node_ids == {"a", "b", "c"}
    assert not result.truncated, "visited set must terminate the cycle"


def test_candidate_edges_are_traversed_but_stay_candidate():
    software_map = SoftwareMap(
        snapshot_id=SNAP,
        nodes=[node("a"), node("b")],
        edges=[edge("a", "b", resolution="candidate")],
    )
    graph = build_graph(software_map)
    result = graph.downstream("a")
    assert result.node_ids == {"a", "b"}
    assert software_map.edges[0].resolution == Resolution.CANDIDATE


def test_node_budget_truncation_is_reported():
    software_map = SoftwareMap(
        snapshot_id=SNAP,
        nodes=[node(f"n{i}") for i in range(10)],
        edges=[edge(f"n{i}", f"n{i+1}") for i in range(9)],
    )
    graph = build_graph(software_map)
    result = graph.downstream("n0", max_nodes=3)
    assert result.truncated is True
    assert result.stop_reason == "max_nodes"
    assert len(result.node_ids) == 3


def test_depth_budget_truncation_is_reported():
    software_map = SoftwareMap(
        snapshot_id=SNAP,
        nodes=[node(f"n{i}") for i in range(5)],
        edges=[edge(f"n{i}", f"n{i+1}") for i in range(4)],
    )
    graph = build_graph(software_map)
    result = graph.downstream("n0", max_depth=2)
    assert result.truncated is True
    assert result.stop_reason == "max_depth"


def test_dangling_edges_are_recorded_not_silent():
    software_map = SoftwareMap(
        snapshot_id=SNAP,
        nodes=[node("a")],
        edges=[edge("a", "ghost"), edge("ghost2", "a")],
    )
    graph = build_graph(software_map)
    assert set(graph.dangling_edges) == {"edge:a->ghost", "edge:ghost2->a"}
    # 孤立节点保留，遍历不崩溃
    result = graph.downstream("a")
    assert result.node_ids == {"a"}


def test_orphan_nodes_survive_in_map():
    software_map = SoftwareMap(snapshot_id=SNAP, nodes=[node("lonely")], edges=[])
    graph = build_graph(software_map)
    assert {n.id for n in graph.software_map.nodes} == {"lonely"}
    assert graph.upstream("lonely").node_ids == {"lonely"}


def test_walk_rejects_unknown_direction():
    graph = build_graph(SoftwareMap(snapshot_id=SNAP))
    with pytest.raises(ValueError):
        graph.walk("a", direction="sideways")


def test_map_contract_rejects_bad_edge_enum():
    with pytest.raises((ValidationError, ValueError)):
        edge("a", "b", relation="vibrates")
