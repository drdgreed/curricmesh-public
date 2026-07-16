"""Directed-graph helpers for the Course Builder dependency engine (Task 4).

The only public symbol is :func:`would_create_cycle`, which tests whether
adding a single new directed edge to an existing edge-set would introduce a
cycle in the resulting DAG.

The algorithm is the same three-colour iterative DFS used in
``app/core/fork.py::_assert_acyclic`` — it is NOT recursive so it cannot blow
the Python call stack on a large dependency graph.
"""

from __future__ import annotations

import uuid
from collections import defaultdict


def would_create_cycle(
    edges: list[tuple[uuid.UUID, uuid.UUID]],
    new_from: uuid.UUID,
    new_to: uuid.UUID,
) -> bool:
    """Return ``True`` if adding ``new_from → new_to`` to *edges* creates a cycle.

    *edges* is the current accepted dependency edge list for a course (pairs of
    (from_item_id, to_item_id)).  The check is purely in-memory: we build a
    transient adjacency dict, insert the proposed edge, and run an iterative
    DFS looking for a back edge.

    Self-loops (``new_from == new_to``) are detected immediately without
    touching the DFS.

    Complexity: O(|nodes| + |edges|) — linear in the size of the graph.
    """
    # A self-loop is always a cycle.
    if new_from == new_to:
        return True

    # Build adjacency list from existing edges + proposed edge.
    adjacency: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for frm, to in edges:
        adjacency[frm].append(to)
    adjacency[new_from].append(new_to)

    # Collect all nodes.
    nodes: set[uuid.UUID] = set(adjacency.keys())
    for frm, to in edges:
        nodes.add(frm)
        nodes.add(to)
    nodes.add(new_from)
    nodes.add(new_to)

    # Three-colour iterative DFS (mirrors fork.py::_assert_acyclic).
    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[uuid.UUID, int] = {n: WHITE for n in nodes}

    for start in list(nodes):
        if color[start] != WHITE:
            continue
        # Stack entries: (node, entered?) — entered=True is the post-visit marker.
        stack: list[tuple[uuid.UUID, bool]] = [(start, False)]
        while stack:
            node, entered = stack.pop()
            if entered:
                color[node] = BLACK
                continue
            if color[node] == GREY:
                continue
            color[node] = GREY
            stack.append((node, True))  # post-visit
            for nxt in adjacency.get(node, ()):
                if color.get(nxt, WHITE) == GREY:
                    return True  # back edge → cycle
                if color.get(nxt, WHITE) == WHITE:
                    stack.append((nxt, False))

    return False
