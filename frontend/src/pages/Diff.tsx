/**
 * Diff page — renders the version diff for a single asset.
 *
 * Route: /assets/:assetId/diff?from=<versionId>&to=<versionId>
 *
 * Features:
 *  - Version pickers (From / To) populated from GET /assets/:assetId/versions
 *  - Text diff: unified view with added lines (green) / removed lines (red)
 *  - Toggle: Unified <-> Side-by-side
 *  - Structured diff: MUI table with Added / Removed / Changed sections
 *  - Loading / error / empty states
 */

import React, { useState, useEffect } from "react";
import { useParams, useSearchParams, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import ButtonGroup from "@mui/material/ButtonGroup";
import FormControl from "@mui/material/FormControl";
import InputLabel from "@mui/material/InputLabel";
import MenuItem from "@mui/material/MenuItem";
import Select from "@mui/material/Select";
import Stack from "@mui/material/Stack";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import Typography from "@mui/material/Typography";
import Alert from "@mui/material/Alert";
import CircularProgress from "@mui/material/CircularProgress";
import Divider from "@mui/material/Divider";
import Paper from "@mui/material/Paper";
import Chip from "@mui/material/Chip";
import { useTheme } from "@mui/material/styles";

import {
  listAssetVersions,
  getDiff,
  type AssetVersionItem,
  type StructuredDiff,
  type ChangedEntry,
} from "../api/client";
import { surfacesByMode } from "../theme";

/** Read `theme.surfaces`, falling back to light tokens when no CurricMesh theme
 *  is mounted (e.g. unit tests render without our ThemeProvider). */
function useSurfaces() {
  const theme = useTheme();
  return theme.surfaces ?? surfacesByMode.light;
}

// ---------------------------------------------------------------------------
// Text diff renderers
// ---------------------------------------------------------------------------

type DiffLineKind = "add" | "remove" | "hunk" | "meta" | "context";

function classifyLine(line: string): DiffLineKind {
  if (line.startsWith("+++") || line.startsWith("---")) return "meta";
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "remove";
  return "context";
}

/**
 * GitHub-style unified diff: a file-header bar, full-width add/remove line
 * backgrounds, a left gutter with old/new line numbers + a +/- marker, all
 * monospace and mode-aware (muted green/red on dark).
 */
function UnifiedDiffView({ unified }: { unified: string }) {
  const s = useSurfaces();
  const lines = unified.split("\n");

  // Track old/new line numbers across hunks for the gutter.
  let oldNo = 0;
  let newNo = 0;

  return (
    <Box
      data-testid="unified-diff"
      sx={{
        fontFamily: "monospace",
        fontSize: 13,
        overflowX: "auto",
        m: 0,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1.5,
        bgcolor: "background.paper",
      }}
    >
      {/* File-header bar */}
      <Box
        sx={{
          px: 1.5,
          py: 0.75,
          bgcolor: s.diffGutterBg,
          borderBottom: "1px solid",
          borderColor: "divider",
          color: "text.secondary",
          fontSize: 12,
          fontWeight: 600,
          fontFamily: "monospace",
          display: "flex",
          alignItems: "center",
          gap: 1,
        }}
      >
        <Box component="span" sx={{ color: s.diffRemovedText }}>
          −removed
        </Box>
        <Box component="span" sx={{ color: s.diffAddedText }}>
          +added
        </Box>
      </Box>

      {lines.map((line, i) => {
        const kind = classifyLine(line);

        let leftNo: string | number = "";
        let rightNo: string | number = "";
        let marker = " ";
        let bg = "transparent";
        let color = "text.primary";

        if (kind === "hunk") {
          // Reset counters from the @@ -a,b +c,d @@ header.
          const m = /@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
          if (m) {
            oldNo = parseInt(m[1], 10);
            newNo = parseInt(m[2], 10);
          }
          bg = s.diffHunkBg;
          color = s.diffHunkText;
        } else if (kind === "meta") {
          color = "text.secondary";
        } else if (kind === "add") {
          rightNo = newNo++;
          marker = "+";
          bg = s.diffAddedBg;
          color = s.diffAddedText;
        } else if (kind === "remove") {
          leftNo = oldNo++;
          marker = "-";
          bg = s.diffRemovedBg;
          color = s.diffRemovedText;
        } else {
          leftNo = oldNo++;
          rightNo = newNo++;
        }

        const isAdd = kind === "add";
        const isRemove = kind === "remove";

        return (
          <Box
            key={i}
            data-testid={
              isAdd ? "diff-added-line" : isRemove ? "diff-removed-line" : undefined
            }
            sx={{
              display: "flex",
              background: bg,
              color,
              whiteSpace: "pre",
              minWidth: "max-content",
              width: "100%",
            }}
          >
            {/* Gutter: old + new line numbers */}
            <Box
              component="span"
              sx={{
                userSelect: "none",
                textAlign: "right",
                width: 36,
                px: 0.5,
                flexShrink: 0,
                color: "text.disabled",
                bgcolor: kind === "hunk" ? "transparent" : s.diffGutterBg,
                opacity: 0.85,
              }}
            >
              {leftNo}
            </Box>
            <Box
              component="span"
              sx={{
                userSelect: "none",
                textAlign: "right",
                width: 36,
                px: 0.5,
                flexShrink: 0,
                color: "text.disabled",
                bgcolor: kind === "hunk" ? "transparent" : s.diffGutterBg,
                opacity: 0.85,
                borderRight: "1px solid",
                borderColor: "divider",
              }}
            >
              {rightNo}
            </Box>
            {/* +/- marker */}
            <Box
              component="span"
              sx={{ userSelect: "none", width: 16, textAlign: "center", flexShrink: 0 }}
            >
              {marker}
            </Box>
            {/* Line content */}
            <Box component="span" sx={{ pr: 1.5, flex: 1 }}>
              {line.replace(/^[+-]/, "")}
            </Box>
          </Box>
        );
      })}
    </Box>
  );
}

/** Side-by-side diff: removed lines on the left, added on the right. */
function SideBySideDiffView({ added, removed }: { added: string[]; removed: string[] }) {
  const s = useSurfaces();
  const maxLen = Math.max(added.length, removed.length);
  const rows = Array.from({ length: maxLen }, (_, i) => ({
    removed: removed[i] ?? "",
    added: added[i] ?? "",
  }));

  return (
    <Box
      data-testid="sidebyside-diff"
      sx={{
        display: "grid",
        gridTemplateColumns: "1fr 1fr",
        gap: 1,
        fontFamily: "monospace",
        fontSize: 13,
        overflowX: "auto",
      }}
    >
      {/* Left column header */}
      <Box sx={{ fontWeight: 600, color: s.diffRemovedText, mb: 0.5 }}>Removed</Box>
      {/* Right column header */}
      <Box sx={{ fontWeight: 600, color: s.diffAddedText, mb: 0.5 }}>Added</Box>

      {rows.map((row, i) => (
        <React.Fragment key={i}>
          <Box
            data-testid="diff-removed-line"
            sx={{
              background: row.removed ? s.diffRemovedBg : "transparent",
              color: row.removed ? s.diffRemovedText : "text.primary",
              px: 0.5,
              whiteSpace: "pre-wrap",
              wordBreak: "break-all",
              borderRadius: 0.25,
              minHeight: "1.4em",
            }}
          >
            {row.removed}
          </Box>
          <Box
            data-testid="diff-added-line"
            sx={{
              background: row.added ? s.diffAddedBg : "transparent",
              color: row.added ? s.diffAddedText : "text.primary",
              px: 0.5,
              whiteSpace: "pre-wrap",
              wordBreak: "break-all",
              borderRadius: 0.25,
              minHeight: "1.4em",
            }}
          >
            {row.added}
          </Box>
        </React.Fragment>
      ))}
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Structured diff table
// ---------------------------------------------------------------------------

function renderCellValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function StructuredDiffTable({ structured }: { structured: StructuredDiff }) {
  const s = useSurfaces();
  const hasAdded = structured.added.length > 0;
  const hasRemoved = structured.removed.length > 0;
  const hasChanged = structured.changed.length > 0;

  if (!hasAdded && !hasRemoved && !hasChanged) {
    return (
      <Typography variant="body2" color="text.secondary">
        No structural changes.
      </Typography>
    );
  }

  return (
    <Box>
      {hasAdded && (
        <Box sx={{ mb: 2 }}>
          <Typography variant="subtitle2" sx={{ color: s.diffAddedText, mb: 0.5 }}>
            Added
          </Typography>
          <Table size="small" sx={{ background: s.diffAddedBg }}>
            <TableHead>
              <TableRow>
                <TableCell>Value</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {structured.added.map((item, i) => (
                <TableRow key={i}>
                  <TableCell sx={{ fontFamily: "monospace", fontSize: 13 }}>
                    {renderCellValue(item)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Box>
      )}

      {hasRemoved && (
        <Box sx={{ mb: 2 }}>
          <Typography variant="subtitle2" sx={{ color: s.diffRemovedText, mb: 0.5 }}>
            Removed
          </Typography>
          <Table size="small" sx={{ background: s.diffRemovedBg }}>
            <TableHead>
              <TableRow>
                <TableCell>Value</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {structured.removed.map((item, i) => (
                <TableRow key={i}>
                  <TableCell sx={{ fontFamily: "monospace", fontSize: 13 }}>
                    {renderCellValue(item)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Box>
      )}

      {hasChanged && (
        <Box>
          <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
            Changed
          </Typography>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Key</TableCell>
                <TableCell>From</TableCell>
                <TableCell>To</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {structured.changed.map((entry: ChangedEntry) => (
                <TableRow key={entry.key} data-testid={`changed-row-${entry.key}`}>
                  <TableCell sx={{ fontFamily: "monospace", fontWeight: 600 }}>
                    {entry.key}
                  </TableCell>
                  <TableCell sx={{ color: s.diffRemovedText, fontFamily: "monospace" }}>
                    {renderCellValue(entry.from)}
                  </TableCell>
                  <TableCell sx={{ color: s.diffAddedText, fontFamily: "monospace" }}>
                    {renderCellValue(entry.to)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Box>
      )}
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Version picker
// ---------------------------------------------------------------------------

function VersionPicker({
  label,
  versions,
  value,
  onChange,
  exclude,
}: {
  label: string;
  versions: AssetVersionItem[];
  value: string;
  onChange: (id: string) => void;
  exclude?: string;
}) {
  const available = exclude ? versions.filter((v) => v.id !== exclude) : versions;

  return (
    <FormControl size="small" sx={{ minWidth: 180 }}>
      <InputLabel>{label}</InputLabel>
      <Select
        label={label}
        value={value}
        onChange={(e) => onChange(e.target.value as string)}
      >
        <MenuItem value="">
          <em>Select version…</em>
        </MenuItem>
        {available.map((v) => (
          <MenuItem key={v.id} value={v.id}>
            v{v.semver}{" "}
            <Chip
              label={v.status}
              size="small"
              sx={{ ml: 1, fontSize: 11, height: 18 }}
            />
          </MenuItem>
        ))}
      </Select>
    </FormControl>
  );
}

// ---------------------------------------------------------------------------
// Main Diff page
// ---------------------------------------------------------------------------

type DiffMode = "unified" | "sidebyside";

export function Diff() {
  const { assetId } = useParams<{ assetId: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();

  const [fromId, setFromId] = useState<string>(searchParams.get("from") ?? "");
  const [toId, setToId] = useState<string>(searchParams.get("to") ?? "");
  const [mode, setMode] = useState<DiffMode>("unified");

  // Sync pickers → query params
  useEffect(() => {
    const next: Record<string, string> = {};
    if (fromId) next.from = fromId;
    if (toId) next.to = toId;
    setSearchParams(next, { replace: true });
  }, [fromId, toId, setSearchParams]);

  // Fetch versions list
  const versionsQuery = useQuery({
    queryKey: ["asset-versions", assetId],
    queryFn: () => listAssetVersions(assetId!),
    enabled: Boolean(assetId),
    retry: false,
  });

  // Fetch diff (only when both IDs are selected)
  const diffEnabled = Boolean(assetId && fromId && toId && fromId !== toId);
  const diffQuery = useQuery({
    queryKey: ["asset-diff", assetId, fromId, toId],
    queryFn: () => getDiff(assetId!, fromId, toId),
    enabled: diffEnabled,
    retry: false,
  });

  // ---------------------------------------------------------------------------
  // Render helpers
  // ---------------------------------------------------------------------------

  function renderVersionsError() {
    const err = versionsQuery.error as { response?: { status: number } } | null;
    if (err?.response?.status === 404) {
      return <Alert severity="error">Asset not found (404).</Alert>;
    }
    return <Alert severity="error">Failed to load versions. Please try again.</Alert>;
  }

  function renderDiffError() {
    const err = diffQuery.error as { response?: { status: number; data?: { detail?: string } } } | null;
    if (err?.response?.status === 404) {
      return (
        <Alert severity="error">
          One or both versions not found, or they belong to different assets (404).
        </Alert>
      );
    }
    if (err?.response?.status === 422) {
      return (
        <Alert severity="error">
          Could not compute diff — version body is malformed (422):{" "}
          {err?.response?.data?.detail ?? ""}
        </Alert>
      );
    }
    return <Alert severity="error">Failed to load diff. Please try again.</Alert>;
  }

  function renderDiffContent() {
    if (!diffEnabled) return null;
    if (diffQuery.isLoading) {
      return (
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, mt: 2 }}>
          <CircularProgress size={18} />
          <Typography variant="body2">Loading diff…</Typography>
        </Box>
      );
    }
    if (diffQuery.isError) {
      return <Box sx={{ mt: 2 }}>{renderDiffError()}</Box>;
    }
    if (!diffQuery.data) return null;

    const diff = diffQuery.data;
    const hasText = diff.text !== null;
    const hasStructured = diff.structured !== null;
    const isEmpty =
      (!hasText ||
        (diff.text!.added.length === 0 &&
          diff.text!.removed.length === 0 &&
          diff.text!.unified.trim() === "")) &&
      (!hasStructured ||
        (diff.structured!.added.length === 0 &&
          diff.structured!.removed.length === 0 &&
          diff.structured!.changed.length === 0));

    if (isEmpty) {
      return (
        <Box sx={{ mt: 2 }}>
          <Alert severity="info">No changes between the selected versions.</Alert>
        </Box>
      );
    }

    return (
      <Box sx={{ mt: 2 }}>
        {/* Asset kind badge */}
        <Box sx={{ mb: 1.5 }}>
          <Chip label={`kind: ${diff.kind}`} size="small" variant="outlined" />
        </Box>

        {/* Text diff section */}
        {hasText && diff.text && (
          <Box sx={{ mb: 3 }}>
            <Stack
              direction="row"
              sx={{ justifyContent: "space-between", alignItems: "center", mb: 1 }}
            >
              <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                Text Diff
              </Typography>
              <ButtonGroup size="small" variant="outlined">
                <Button
                  onClick={() => setMode("unified")}
                  variant={mode === "unified" ? "contained" : "outlined"}
                  data-testid="toggle-unified"
                >
                  Unified
                </Button>
                <Button
                  onClick={() => setMode("sidebyside")}
                  variant={mode === "sidebyside" ? "contained" : "outlined"}
                  data-testid="toggle-sidebyside"
                >
                  Side-by-side
                </Button>
              </ButtonGroup>
            </Stack>

            <Paper variant="outlined" sx={{ p: 1 }}>
              {mode === "unified" ? (
                <UnifiedDiffView unified={diff.text.unified} />
              ) : (
                <SideBySideDiffView
                  added={diff.text.added}
                  removed={diff.text.removed}
                />
              )}
            </Paper>
          </Box>
        )}

        {hasText && hasStructured && <Divider sx={{ my: 2 }} />}

        {/* Structured diff section */}
        {hasStructured && diff.structured && (
          <Box>
            <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
              Structured Diff
            </Typography>
            <Paper variant="outlined" sx={{ p: 1.5 }}>
              <StructuredDiffTable structured={diff.structured} />
            </Paper>
          </Box>
        )}
      </Box>
    );
  }

  // ---------------------------------------------------------------------------
  // Main render
  // ---------------------------------------------------------------------------

  return (
    <Box sx={{ p: 3 }}>
      <Stack direction="row" sx={{ alignItems: "center", mb: 2, gap: 1 }}>
        <Button variant="text" size="small" onClick={() => navigate(-1)}>
          ← Back
        </Button>
        <Typography variant="h5" sx={{ fontWeight: 600 }}>
          Version Diff
        </Typography>
        {assetId && (
          <Typography
            variant="caption"
            sx={{ fontFamily: "monospace", color: "text.secondary" }}
          >
            asset: {assetId}
          </Typography>
        )}
      </Stack>

      {/* Version selection */}
      {versionsQuery.isLoading && (
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 2 }}>
          <CircularProgress size={18} />
          <Typography variant="body2">Loading versions…</Typography>
        </Box>
      )}

      {versionsQuery.isError && (
        <Box sx={{ mb: 2 }}>{renderVersionsError()}</Box>
      )}

      {versionsQuery.data && versionsQuery.data.length === 0 && (
        <Box sx={{ mb: 2 }}>
          <Alert severity="info">This asset has no versions to compare.</Alert>
        </Box>
      )}

      {versionsQuery.data && versionsQuery.data.length === 1 && (
        <Box sx={{ mb: 2 }}>
          <Alert severity="info">
            Only one version exists — at least two are needed to diff.
          </Alert>
        </Box>
      )}

      {versionsQuery.data && versionsQuery.data.length >= 2 && (
        <Stack direction="row" spacing={2} sx={{ mb: 2, flexWrap: "wrap" }}>
          <VersionPicker
            label="From (before)"
            versions={versionsQuery.data}
            value={fromId}
            onChange={setFromId}
            exclude={toId}
          />
          <VersionPicker
            label="To (after)"
            versions={versionsQuery.data}
            value={toId}
            onChange={setToId}
            exclude={fromId}
          />
          {fromId && toId && fromId === toId && (
            <Alert severity="warning" sx={{ alignSelf: "center", py: 0 }}>
              From and To must be different versions.
            </Alert>
          )}
        </Stack>
      )}

      {renderDiffContent()}
    </Box>
  );
}
