/**
 * Graph page — shows the dependency graph for the first available curriculum.
 *
 * Uses useQuery to fetch from GET /api/v1/curricula/{id}/graph.
 * Renders <DependencyGraph> with loading, error, and empty states.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import CircularProgress from "@mui/material/CircularProgress";
import FormControl from "@mui/material/FormControl";
import InputLabel from "@mui/material/InputLabel";
import MenuItem from "@mui/material/MenuItem";
import Select from "@mui/material/Select";
import Typography from "@mui/material/Typography";

import { getDashboard, getGraph } from "../api/client";
import { DependencyGraph } from "../components/DependencyGraph";

export function Graph() {
  // Fetch all curricula via the dashboard endpoint (already typed and cached)
  const {
    data: dashboardData,
    isLoading: isDashLoading,
    isError: isDashError,
  } = useQuery({
    queryKey: ["dashboard"],
    queryFn: getDashboard,
  });

  const curricula = dashboardData?.curricula ?? [];

  // Default to first curriculum that has a current_version_id; fallback to first
  const defaultId =
    curricula.find((c) => c.current_version_id)?.id ?? curricula[0]?.id ?? "";

  const [selectedId, setSelectedId] = useState<string>("");

  // Resolve the curriculum ID to use — prefer picker value, then default
  const curriculumId = selectedId || defaultId;

  const {
    data: graphData,
    isLoading: isGraphLoading,
    isError: isGraphError,
  } = useQuery({
    queryKey: ["graph", curriculumId],
    queryFn: () => getGraph(curriculumId),
    enabled: !!curriculumId,
  });

  if (isDashLoading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", mt: 8 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (isDashError) {
    return (
      <Alert severity="error">Failed to load curricula. Please try again.</Alert>
    );
  }

  if (curricula.length === 0) {
    return (
      <Alert severity="info">
        No curricula found. Create a curriculum to see the dependency graph.
      </Alert>
    );
  }

  return (
    <Box>
      <Typography variant="h5">Dependency Graph</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Visualize how assets depend on one another. Stale nodes are flagged in red.
      </Typography>

      {/* Curriculum picker */}
      {curricula.length > 1 && (
        <FormControl size="small" sx={{ mb: 2, minWidth: 280 }}>
          <InputLabel id="curriculum-select-label">Curriculum</InputLabel>
          <Select
            labelId="curriculum-select-label"
            value={curriculumId}
            label="Curriculum"
            onChange={(e) => setSelectedId(e.target.value)}
          >
            {curricula.map((c) => (
              <MenuItem key={c.id} value={c.id}>
                {c.name}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      )}

      {/* Graph area */}
      {isGraphLoading && (
        <Box sx={{ display: "flex", justifyContent: "center", mt: 4 }}>
          <CircularProgress />
        </Box>
      )}

      {isGraphError && (
        <Alert severity="error">
          Failed to load dependency graph. Please try again.
        </Alert>
      )}

      {graphData && !isGraphLoading && !isGraphError && (
        <>
          {graphData.nodes.length === 0 ? (
            <Alert severity="info">
              This curriculum has no active version or no assets yet.
            </Alert>
          ) : (
            <DependencyGraph
              nodes={graphData.nodes}
              edges={graphData.edges}
              misalignedAssetIds={graphData.misaligned_asset_ids}
            />
          )}
        </>
      )}
    </Box>
  );
}
