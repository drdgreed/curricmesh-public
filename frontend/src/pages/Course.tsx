/**
 * Course page — a calendar/week view of a curriculum's active version, built
 * from the immutable manifest (GET /curricula/{id}/calendar). Each week is a
 * section of clickable asset tiles; clicking a tile opens <AssetDetailDrawer>
 * with the content, source link, prerequisites/dependents, and revision history.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import FormControl from "@mui/material/FormControl";
import InputLabel from "@mui/material/InputLabel";
import MenuItem from "@mui/material/MenuItem";
import Select from "@mui/material/Select";
import Typography from "@mui/material/Typography";
import { useTheme } from "@mui/material/styles";
import LinkIcon from "@mui/icons-material/Link";
import WarningAmberIcon from "@mui/icons-material/WarningAmber";

import type { AssetKind, CalendarTile } from "../api/client";
import {
  ASSET_KIND_LABELS,
  getCourseCalendar,
  getDashboard,
} from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { surfacesByMode } from "../theme";
import { AssetDetailDrawer } from "../components/AssetDetailDrawer";

function kindLabel(kind: string): string {
  return ASSET_KIND_LABELS[kind as AssetKind] ?? kind;
}

function Tile({
  tile,
  onClick,
}: {
  tile: CalendarTile;
  onClick: () => void;
}) {
  const theme = useTheme();
  const s = theme.surfaces ?? surfacesByMode.light;
  return (
    <Box
      onClick={onClick}
      data-testid="course-tile"
      sx={{
        cursor: "pointer",
        p: 1.5,
        borderRadius: 2,
        bgcolor: "background.paper",
        border: "1px solid",
        borderColor: tile.misaligned ? "warning.main" : "divider",
        boxShadow: tile.misaligned ? s.tileMisalignedRing : s.tileShadow,
        transition: "all 0.12s ease",
        "&:hover": {
          borderColor: "primary.main",
          boxShadow: s.tileShadowHover,
          transform: "translateY(-1px)",
        },
        display: "flex",
        flexDirection: "column",
        gap: 0.75,
        minHeight: 96,
      }}
    >
      <Box sx={{ display: "flex", justifyContent: "space-between", gap: 1 }}>
        <Chip
          label={kindLabel(tile.kind)}
          size="small"
          variant="outlined"
          sx={{ height: 22, fontSize: 11 }}
        />
        {tile.misaligned && (
          <WarningAmberIcon
            sx={{ fontSize: 18, color: "warning.main" }}
            titleAccess="Out of alignment"
          />
        )}
      </Box>
      <Typography variant="body2" sx={{ fontWeight: 600, lineHeight: 1.3 }}>
        {tile.label}
      </Typography>
      <Box sx={{ mt: "auto", display: "flex", alignItems: "center", gap: 1 }}>
        {tile.latest_version && (
          <Typography variant="caption" color="text.secondary">
            v{tile.latest_version}
          </Typography>
        )}
        {tile.source_url && (
          <LinkIcon sx={{ fontSize: 14, color: "text.secondary" }} />
        )}
      </Box>
    </Box>
  );
}

export function Course() {
  const { role } = useAuth();
  const [selectedId, setSelectedId] = useState<string>("");
  const [activeAssetId, setActiveAssetId] = useState<string | null>(null);

  const { data: dashboard, isLoading: dashLoading } = useQuery({
    queryKey: ["dashboard"],
    queryFn: getDashboard,
  });
  const curricula = dashboard?.curricula ?? [];
  const defaultId =
    curricula.find((c) => c.current_version_id)?.id ?? curricula[0]?.id ?? "";
  const curriculumId = selectedId || defaultId;

  const {
    data: calendar,
    isLoading,
    isError,
  } = useQuery({
    queryKey: ["course-calendar", curriculumId],
    queryFn: () => getCourseCalendar(curriculumId),
    enabled: !!curriculumId,
  });

  if (dashLoading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", mt: 8 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (curricula.length === 0) {
    return <Alert severity="info">No curricula found.</Alert>;
  }

  // Weeks lead (ascending); the week_index==0 "Projects" bucket trails at the end.
  const sections = [...(calendar?.sections ?? [])].sort((a, b) => {
    const wa = a.week_index === 0 ? Infinity : a.week_index;
    const wb = b.week_index === 0 ? Infinity : b.week_index;
    return wa - wb;
  });

  return (
    <Box>
      <Typography variant="h5" gutterBottom>
        Course
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Browse the active version week by week. Click any tile to open its
        content, source link, dependencies, and revision history.
      </Typography>

      {curricula.length > 1 && (
        <FormControl size="small" sx={{ mb: 2.5, minWidth: 280 }}>
          <InputLabel id="course-curriculum-label">Curriculum</InputLabel>
          <Select
            labelId="course-curriculum-label"
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

      {isLoading && (
        <Box sx={{ display: "flex", justifyContent: "center", mt: 4 }}>
          <CircularProgress />
        </Box>
      )}
      {isError && (
        <Alert severity="error">Failed to load the course calendar.</Alert>
      )}

      {calendar && !isLoading && sections.length === 0 && (
        <Alert severity="info">
          This curriculum has no active version content yet.
        </Alert>
      )}

      <Box>
        {sections.map((section) => (
          <Box key={`${section.week_index}-${section.section}`} sx={{ mb: 3 }}>
            <Box
              sx={{
                display: "flex",
                alignItems: "baseline",
                gap: 1.5,
                mb: 1.25,
              }}
            >
              <Chip
                label={
                  section.week_index > 0
                    ? `Week ${section.week_index}`
                    : "Projects"
                }
                size="small"
                color="primary"
                sx={{ fontWeight: 600 }}
              />
              <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                {section.section}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {section.tiles.length} item
                {section.tiles.length === 1 ? "" : "s"}
              </Typography>
            </Box>
            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: {
                  xs: "1fr",
                  sm: "repeat(2, 1fr)",
                  md: "repeat(3, 1fr)",
                  lg: "repeat(4, 1fr)",
                },
                gap: 1.5,
              }}
            >
              {section.tiles.map((tile) => (
                <Tile
                  key={tile.id}
                  tile={tile}
                  onClick={() => setActiveAssetId(tile.id)}
                />
              ))}
            </Box>
          </Box>
        ))}
      </Box>

      <AssetDetailDrawer
        assetId={activeAssetId}
        curriculumId={curriculumId}
        role={role}
        onClose={() => setActiveAssetId(null)}
        onNavigate={(id) => setActiveAssetId(id)}
      />
    </Box>
  );
}
