/**
 * Changes page — a GitHub-PR-style view of "what changed in the current
 * version" of a curriculum. Resolves the active CurriculumVersion (via
 * GET /curricula/{id}/active-version) and diffs it against its parent
 * (GET /curricula/{id}/versions/{head}/diff, base defaults to the parent).
 *
 * Sections: Added (green), Removed (red), Changed (amber, with rev seq +
 * short hashes), Edges +/−. Colors come from the mode-aware `surfaces` /
 * `warning` theme tokens so light and dark both read cleanly. A root version
 * (no parent) renders a clean empty state.
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
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { useTheme } from "@mui/material/styles";

import type {
  VersionDiffAsset,
  VersionDiffChanged,
  VersionDiffEdge,
} from "../api/client";
import { getActiveVersion, getDashboard, getVersionDiff } from "../api/client";
import { PageHeader } from "../components/PageHeader";
import { surfacesByMode } from "../theme";

function shortHash(h: string): string {
  return h.slice(0, 8);
}

/** A titled section that only renders when it has rows. */
function DiffSection({
  title,
  count,
  color,
  children,
}: {
  title: string;
  count: number;
  color: string;
  children: React.ReactNode;
}) {
  if (count === 0) return null;
  return (
    <Box>
      <Typography
        variant="subtitle1"
        sx={{ fontWeight: 700, color, mb: 0.75 }}
      >
        {title} ({count})
      </Typography>
      <Stack spacing={0.5}>{children}</Stack>
    </Box>
  );
}

function AssetRow({
  asset,
  bg,
  color,
  sign,
}: {
  asset: VersionDiffAsset;
  bg: string;
  color: string;
  sign: "+" | "−";
}) {
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        px: 1.25,
        py: 0.75,
        borderRadius: 1.5,
        bgcolor: bg,
      }}
    >
      <Box component="span" sx={{ color, fontWeight: 700, width: 14 }}>
        {sign}
      </Box>
      <Typography variant="body2" sx={{ fontWeight: 500, flex: 1 }}>
        {asset.label}
      </Typography>
      <Typography
        variant="caption"
        sx={{ fontFamily: "monospace", color: "text.secondary" }}
      >
        rev {asset.seq} · {shortHash(asset.content_hash)}
      </Typography>
    </Box>
  );
}

function ChangedRow({
  change,
  bg,
  color,
}: {
  change: VersionDiffChanged;
  bg: string;
  color: string;
}) {
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        px: 1.25,
        py: 0.75,
        borderRadius: 1.5,
        bgcolor: bg,
      }}
    >
      <Box component="span" sx={{ color, fontWeight: 700, width: 14 }}>
        ~
      </Box>
      <Typography variant="body2" sx={{ fontWeight: 500, flex: 1 }}>
        {change.label}
      </Typography>
      <Typography
        variant="caption"
        sx={{ fontFamily: "monospace", color: "text.secondary" }}
      >
        rev {change.from_seq} → {change.to_seq} · {shortHash(change.from_hash)} →{" "}
        {shortHash(change.to_hash)}
      </Typography>
    </Box>
  );
}

function EdgeRow({
  edge,
  bg,
  color,
  sign,
}: {
  edge: VersionDiffEdge;
  bg: string;
  color: string;
  sign: "+" | "−";
}) {
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        px: 1.25,
        py: 0.6,
        borderRadius: 1.5,
        bgcolor: bg,
      }}
    >
      <Box component="span" sx={{ color, fontWeight: 700, width: 14 }}>
        {sign}
      </Box>
      <Typography variant="body2" sx={{ flex: 1 }}>
        {edge.from_label} → {edge.to_label}
      </Typography>
      <Chip
        label={edge.edge_type}
        size="small"
        variant="outlined"
        sx={{ height: 20, fontSize: 11 }}
      />
    </Box>
  );
}

export function Changes() {
  const theme = useTheme();
  const s = theme.surfaces ?? surfacesByMode.light;
  const amber = theme.palette.warning.main;

  const [selectedId, setSelectedId] = useState<string>("");

  const { data: dashboard, isLoading: dashLoading } = useQuery({
    queryKey: ["dashboard"],
    queryFn: getDashboard,
  });
  const curricula = dashboard?.curricula ?? [];
  const defaultId =
    curricula.find((c) => c.current_version_id)?.id ?? curricula[0]?.id ?? "";
  const curriculumId = selectedId || defaultId;

  const { data: active, isLoading: activeLoading } = useQuery({
    queryKey: ["active-version", curriculumId],
    queryFn: () => getActiveVersion(curriculumId),
    enabled: !!curriculumId,
  });

  const {
    data: diff,
    isLoading: diffLoading,
    isError: diffError,
  } = useQuery({
    queryKey: ["version-diff", curriculumId, active?.head_version_id],
    queryFn: () => getVersionDiff(curriculumId, active!.head_version_id),
    enabled: !!curriculumId && !!active?.head_version_id,
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

  const loading = activeLoading || diffLoading;
  const isRoot = diff != null && diff.base_version_id == null;
  const totalChanges =
    diff == null
      ? 0
      : diff.assets_added.length +
        diff.assets_removed.length +
        diff.assets_changed.length +
        diff.edges_added.length +
        diff.edges_removed.length;

  return (
    <Box>
      <PageHeader
        title="Changes"
        subtitle="What changed in the current version, compared to its parent."
        actions={
          active && (
            <Chip
              label={`v${active.semver} · ${active.status}`}
              color="primary"
              variant="outlined"
            />
          )
        }
      />

      {curricula.length > 1 && (
        <FormControl size="small" sx={{ mb: 2.5, minWidth: 280 }}>
          <InputLabel id="changes-curriculum-label">Curriculum</InputLabel>
          <Select
            labelId="changes-curriculum-label"
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

      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", mt: 4 }}>
          <CircularProgress />
        </Box>
      )}

      {diffError && (
        <Alert severity="error">Failed to load the version diff.</Alert>
      )}

      {!loading && diff && (
        <Box data-testid="changes-view">
          {isRoot ? (
            <Alert severity="info">
              This is the curriculum's root version — there's no parent to
              compare against yet.
            </Alert>
          ) : totalChanges === 0 ? (
            <Alert severity="success">
              No structural changes between this version and its parent.
            </Alert>
          ) : (
            <Stack spacing={3}>
              <DiffSection
                title="Added"
                count={diff.assets_added.length}
                color={s.diffAddedText}
              >
                {diff.assets_added.map((a) => (
                  <AssetRow
                    key={a.asset_id}
                    asset={a}
                    bg={s.diffAddedBg}
                    color={s.diffAddedText}
                    sign="+"
                  />
                ))}
              </DiffSection>

              <DiffSection
                title="Removed"
                count={diff.assets_removed.length}
                color={s.diffRemovedText}
              >
                {diff.assets_removed.map((a) => (
                  <AssetRow
                    key={a.asset_id}
                    asset={a}
                    bg={s.diffRemovedBg}
                    color={s.diffRemovedText}
                    sign="−"
                  />
                ))}
              </DiffSection>

              <DiffSection
                title="Changed"
                count={diff.assets_changed.length}
                color={amber}
              >
                {diff.assets_changed.map((c) => (
                  <ChangedRow
                    key={c.asset_id}
                    change={c}
                    bg={theme.palette.warning.light + "22"}
                    color={amber}
                  />
                ))}
              </DiffSection>

              <DiffSection
                title="Edges added"
                count={diff.edges_added.length}
                color={s.diffAddedText}
              >
                {diff.edges_added.map((e, i) => (
                  <EdgeRow
                    key={`ea-${i}`}
                    edge={e}
                    bg={s.diffAddedBg}
                    color={s.diffAddedText}
                    sign="+"
                  />
                ))}
              </DiffSection>

              <DiffSection
                title="Edges removed"
                count={diff.edges_removed.length}
                color={s.diffRemovedText}
              >
                {diff.edges_removed.map((e, i) => (
                  <EdgeRow
                    key={`er-${i}`}
                    edge={e}
                    bg={s.diffRemovedBg}
                    color={s.diffRemovedText}
                    sign="−"
                  />
                ))}
              </DiffSection>
            </Stack>
          )}
        </Box>
      )}
    </Box>
  );
}

export default Changes;
