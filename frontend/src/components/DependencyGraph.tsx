/**
 * DependencyGraph — renders a curriculum's asset dependency graph using ReactFlow.
 *
 * Layout: simple layered positioning computed from BFS depth on the dependency graph.
 * Misaligned nodes get a red border/background to flag staleness.
 * Clicking a node opens a detail side panel.
 */

import { useCallback, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  type Node,
  type Edge,
  type NodeMouseHandler,
} from "reactflow";
import "reactflow/dist/style.css";

import Box from "@mui/material/Box";
import Drawer from "@mui/material/Drawer";
import Divider from "@mui/material/Divider";
import IconButton from "@mui/material/IconButton";
import Typography from "@mui/material/Typography";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import { useTheme, type Theme } from "@mui/material/styles";
import type { GraphNode as ApiNode, GraphEdge as ApiEdge } from "../api/client";
import { surfacesByMode } from "../theme";
import { StatusBadge } from "./StatusBadge";

interface Props {
  nodes: ApiNode[];
  edges: ApiEdge[];
  misalignedAssetIds: string[];
}

// ---------------------------------------------------------------------------
// Layout — BFS layering from root nodes (nodes with no incoming edges)
// ---------------------------------------------------------------------------

function computeLayout(
  nodes: ApiNode[],
  edges: ApiEdge[]
): Record<string, { x: number; y: number }> {
  const X_SPACING = 280;
  const Y_SPACING = 184;

  // Build adjacency: from → to
  const outgoing = new Map<string, string[]>();
  const inDegree = new Map<string, number>();

  for (const n of nodes) {
    outgoing.set(n.id, []);
    inDegree.set(n.id, 0);
  }

  for (const e of edges) {
    outgoing.get(e.from_asset_id)?.push(e.to_asset_id);
    inDegree.set(e.to_asset_id, (inDegree.get(e.to_asset_id) ?? 0) + 1);
  }

  // Roots: nodes with no incoming edges
  const roots = nodes.filter((n) => inDegree.get(n.id) === 0).map((n) => n.id);
  if (roots.length === 0 && nodes.length > 0) {
    // Cycle or all have incoming — fall back to first node as root
    roots.push(nodes[0].id);
  }

  // BFS to assign depth layer
  const depth = new Map<string, number>();
  const queue: string[] = [...roots];
  for (const r of roots) depth.set(r, 0);

  while (queue.length > 0) {
    const current = queue.shift()!;
    const d = depth.get(current) ?? 0;
    for (const neighbor of outgoing.get(current) ?? []) {
      if (!depth.has(neighbor) || depth.get(neighbor)! < d + 1) {
        depth.set(neighbor, d + 1);
        queue.push(neighbor);
      }
    }
  }

  // Count nodes per layer for horizontal distribution
  const layerCount = new Map<number, number>();
  const layerIndex = new Map<string, number>();

  for (const n of nodes) {
    const d = depth.get(n.id) ?? 0;
    const idx = layerCount.get(d) ?? 0;
    layerIndex.set(n.id, idx);
    layerCount.set(d, idx + 1);
  }

  // Compute pixel positions
  const positions: Record<string, { x: number; y: number }> = {};
  for (const n of nodes) {
    const layer = depth.get(n.id) ?? 0;
    const idx = layerIndex.get(n.id) ?? 0;
    const total = layerCount.get(layer) ?? 1;
    positions[n.id] = {
      x: idx * X_SPACING - ((total - 1) * X_SPACING) / 2,
      y: layer * Y_SPACING,
    };
  }

  return positions;
}

// ---------------------------------------------------------------------------
// Node label component rendered by ReactFlow (plain HTML inside node)
// ---------------------------------------------------------------------------

function nodeLabel(
  apiNode: ApiNode,
  isMisaligned: boolean,
  theme: Theme
): React.ReactNode {
  const s = theme.surfaces ?? surfacesByMode.light;
  return (
    <Box
      sx={{
        px: 1.5,
        py: 1,
        borderRadius: 1.5,
        background: isMisaligned ? s.graphNodeMisalignedBg : s.graphNodeBg,
        // Every node is a clearly-bordered card; misaligned nodes stand out with
        // a thicker red border + tinted background + a "stale" flag.
        border: isMisaligned
          ? `2px solid ${s.graphNodeMisalignedBorder}`
          : `1.5px solid ${s.graphNodeBorder}`,
        boxShadow:
          theme.palette.mode === "dark"
            ? "0 1px 3px rgba(0,0,0,0.5)"
            : "0 1px 3px rgba(15, 23, 42, 0.15)",
        color: theme.palette.text.primary,
        width: 210,
        maxHeight: 168,
        overflow: "hidden",
        boxSizing: "border-box",
        cursor: "pointer",
      }}
    >
      <Typography
        variant="caption"
        sx={{ color: "text.secondary", textTransform: "uppercase", fontSize: 10 }}
      >
        {apiNode.kind.replace(/_/g, " ")}
      </Typography>
      <Typography
        variant="body2"
        sx={{ fontWeight: 600, wordBreak: "break-word", overflowWrap: "anywhere" }}
      >
        {apiNode.label}
      </Typography>
      {apiNode.latest_version && (
        <Typography variant="caption" sx={{ color: "text.secondary" }}>
          v{apiNode.latest_version}
        </Typography>
      )}
      {apiNode.status && (
        <Box sx={{ mt: 0.5 }}>
          <StatusBadge status={apiNode.status} />
        </Box>
      )}
      {isMisaligned && (
        <Typography variant="caption" sx={{ color: "error.main", display: "block", mt: 0.5 }}>
          ⚠ stale
        </Typography>
      )}
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function DependencyGraph({ nodes: apiNodes, edges: apiEdges, misalignedAssetIds }: Props) {
  const navigate = useNavigate();
  const theme = useTheme();
  const s = theme.surfaces ?? surfacesByMode.light;

  const misalignedSet = useMemo(
    () => new Set(misalignedAssetIds),
    [misalignedAssetIds]
  );

  const [selectedNode, setSelectedNode] = useState<ApiNode | null>(null);

  const positions = useMemo(
    () => computeLayout(apiNodes, apiEdges),
    [apiNodes, apiEdges]
  );

  // Build ReactFlow nodes
  const rfNodes: Node[] = useMemo(
    () =>
      apiNodes.map((n) => {
        const isMisaligned = misalignedSet.has(n.id);
        const pos = positions[n.id] ?? { x: 0, y: 0 };
        return {
          id: n.id,
          position: pos,
          data: {
            label: nodeLabel(n, isMisaligned, theme),
            _apiNode: n,
          },
          style: {
            background: "transparent",
            border: "none",
            padding: 0,
          },
        };
      }),
    [apiNodes, misalignedSet, positions, theme]
  );

  // Build ReactFlow edges
  const rfEdges: Edge[] = useMemo(
    () =>
      apiEdges.map((e, idx) => {
        // Capitalize each word of the edge type for legible demo labels:
        // "prerequisite" → "Prerequisite", "depends_on" → "Depends On"
        const labelText = e.edge_type
          .replace(/_/g, " ")
          .replace(/\b\w/g, (c) => c.toUpperCase());
        return {
          id: `edge-${idx}-${e.from_asset_id}-${e.to_asset_id}`,
          source: e.from_asset_id,
          target: e.to_asset_id,
          label: labelText,
          type: "smoothstep",
          animated: false,
          style: { stroke: s.graphEdge },
          labelStyle: { fontSize: 14, fontWeight: 800, fill: s.graphEdgeLabel },
          labelBgStyle: {
            fill: theme.palette.background.paper,
            fillOpacity: 1,
          },
          labelBgPadding: [6, 3] as [number, number],
          labelBgBorderRadius: 4,
        };
      }),
    [apiEdges, s, theme]
  );

  const onNodeClick: NodeMouseHandler = useCallback(
    (_event, node) => {
      setSelectedNode(node.data._apiNode as ApiNode);
    },
    []
  );

  return (
    <Box sx={{ display: "flex", height: "70vh", position: "relative" }}>
      <Box
        sx={{
          flex: 1,
          // Keep ReactFlow's control buttons + edge labels legible in dark mode.
          "& .react-flow__controls-button": {
            backgroundColor: "background.paper",
            borderBottomColor: "divider",
            "& svg": { fill: "currentColor" },
            color: "text.primary",
          },
          "& .react-flow__edge-text": {
            fill: s.graphEdgeLabel,
          },
          "& .react-flow__edge-textbg": {
            fill: theme.palette.background.default,
          },
        }}
      >
        <ReactFlow
          nodes={rfNodes}
          edges={rfEdges}
          onNodeClick={onNodeClick}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          attributionPosition="bottom-right"
        >
          <Background color={theme.palette.divider} />
          <Controls
            style={{
              // ReactFlow controls default to white; tint for dark mode.
              background: theme.palette.background.paper,
            }}
          />
          <MiniMap
            nodeColor={s.graphNodeBorder}
            maskColor={
              theme.palette.mode === "dark"
                ? "rgba(0,0,0,0.6)"
                : "rgba(240,242,245,0.7)"
            }
            style={{ background: theme.palette.background.paper }}
          />
        </ReactFlow>
      </Box>

      {/* Detail side panel */}
      <Drawer
        anchor="right"
        open={selectedNode !== null}
        onClose={() => setSelectedNode(null)}
        variant="persistent"
        sx={{
          "& .MuiDrawer-paper": {
            width: 280,
            position: "absolute",
            height: "100%",
          },
        }}
      >
        {selectedNode && (
          <Box sx={{ p: 2 }}>
            <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "center" }}>
              <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                Asset Detail
              </Typography>
              <IconButton size="small" onClick={() => setSelectedNode(null)} aria-label="close">
                ✕
              </IconButton>
            </Stack>
            <Divider sx={{ my: 1 }} />

            <Typography variant="caption" color="text.secondary" sx={{ textTransform: "uppercase" }}>
              Kind
            </Typography>
            <Typography variant="body2" sx={{ mb: 1 }}>
              {selectedNode.kind.replace(/_/g, " ")}
            </Typography>

            <Typography variant="caption" color="text.secondary" sx={{ textTransform: "uppercase" }}>
              Asset
            </Typography>
            <Typography variant="body2" sx={{ mb: 1 }}>
              {selectedNode.label}
            </Typography>

            <Typography variant="caption" color="text.secondary" sx={{ textTransform: "uppercase" }}>
              Latest Version
            </Typography>
            <Typography variant="body2" sx={{ mb: 1 }}>
              {selectedNode.latest_version ? `v${selectedNode.latest_version}` : "—"}
            </Typography>

            <Typography variant="caption" color="text.secondary" sx={{ textTransform: "uppercase" }}>
              Status
            </Typography>
            <Box sx={{ mt: 0.5, mb: 2 }}>
              {selectedNode.status ? (
                <StatusBadge status={selectedNode.status} />
              ) : (
                <Typography variant="body2" color="text.secondary">—</Typography>
              )}
            </Box>

            {misalignedSet.has(selectedNode.id) && (
              <Typography variant="body2" color="error" sx={{ mb: 2 }}>
                ⚠ This asset is stale relative to one or more of its dependencies.
              </Typography>
            )}

            <Button
              variant="outlined"
              size="small"
              fullWidth
              onClick={() => navigate(`/assets/${selectedNode.id}/diff`)}
            >
              View Diffs
            </Button>
          </Box>
        )}
      </Drawer>
    </Box>
  );
}
