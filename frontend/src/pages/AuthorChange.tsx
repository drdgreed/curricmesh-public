/**
 * AuthorChange page ("Propose Change") — Feature B: rich, structured change
 * authoring that compiles to a release.
 *
 * Authors compose a change-set against a curriculum's active version:
 *   - Edit existing assets  → contributes to `changed[]` (keyed by lineage_key).
 *   - Add new assets        → `added[]`, with calendar placement + dependency
 *                             edges (prerequisites and dependents) → `edges_added[]`.
 * "Execute" POSTs the whole change-set to
 * POST /curricula/{id}/releases (the "merge the PR" action), producing a new
 * active version. Gated to architect / program_manager (the API also enforces
 * 403). Server validation errors (422 cycle/dangling/placement, 409 stale) are
 * surfaced verbatim — fail-closed, nothing is released on error.
 */

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link as RouterLink } from "react-router-dom";
import { AxiosError } from "axios";

import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Autocomplete from "@mui/material/Autocomplete";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Divider from "@mui/material/Divider";
import FormControl from "@mui/material/FormControl";
import FormControlLabel from "@mui/material/FormControlLabel";
import IconButton from "@mui/material/IconButton";
import InputLabel from "@mui/material/InputLabel";
import Link from "@mui/material/Link";
import MenuItem from "@mui/material/MenuItem";
import Radio from "@mui/material/Radio";
import RadioGroup from "@mui/material/RadioGroup";
import Select from "@mui/material/Select";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutlineOutlined";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";

import type {
  AssetKind,
  BumpType,
  CalendarTile,
  CCROut,
  CognitiveLoad,
  ImpactReport,
  ReleaseChangedItem,
  ReleaseAddedItem,
  ReleaseChangeSet,
  ReleaseCreate,
  ReleaseEdge,
  ReleaseResponse,
} from "../api/client";
import {
  analyzeImpact,
  ASSET_KINDS,
  ASSET_KIND_LABELS,
  createCCR,
  createRelease,
  getCourseCalendar,
  getDashboard,
} from "../api/client";
import { useAuth } from "../auth/AuthContext";

const AUTHOR_ROLES = new Set(["architect", "program_manager"]);
const BUMPS: BumpType[] = ["major", "minor", "patch"];

function kindLabel(kind: string): string {
  return ASSET_KIND_LABELS[kind as AssetKind] ?? kind;
}

type ChipColor = "default" | "success" | "warning" | "error";

/** Duration-delta chip: increase=warning, decrease=success, zero=default. */
function durationChip(deltaMinutes: number): { label: string; color: ChipColor } {
  if (deltaMinutes > 0) return { label: `+${deltaMinutes} min`, color: "warning" };
  if (deltaMinutes < 0)
    return { label: `−${Math.abs(deltaMinutes)} min`, color: "success" };
  return { label: "no change", color: "default" };
}

const COGNITIVE_LOAD_COLOR: Record<CognitiveLoad, ChipColor> = {
  lower: "success",
  unchanged: "default",
  higher: "warning",
  much_higher: "error",
};

const COGNITIVE_LOAD_LABEL: Record<CognitiveLoad, string> = {
  lower: "lower",
  unchanged: "unchanged",
  higher: "higher",
  much_higher: "much higher",
};

function extractApiError(err: unknown): string {
  if (err instanceof AxiosError) {
    const status = err.response?.status;
    const detail = err.response?.data?.detail;
    const detailStr =
      typeof detail === "string"
        ? detail
        : detail
          ? JSON.stringify(detail)
          : null;
    if (status === 403) {
      return detailStr ?? "You are not authorized to execute releases.";
    }
    if (status === 409) {
      return `Conflict: ${detailStr ?? "the active version changed underneath you. Reload and retry."}`;
    }
    if (status === 422) {
      return `Invalid change-set: ${detailStr ?? "check placements, edges, and keys."}`;
    }
    if (status === 404) {
      return detailStr ?? "Curriculum not found.";
    }
    if (detailStr) return detailStr;
    return err.message;
  }
  return "Failed to execute the release. Please try again.";
}

// --- staged-row shapes (local UI state) ---------------------------------

interface ChangedRow {
  lineage_key: string;
  label: string;
  kind: string;
  content: string;
}

interface StagedAdd {
  lineage_key: string;
  kind: AssetKind;
  content: string;
  week_index: number;
  section: string;
  order: number;
  source_url: string;
  prereqs: string[]; // lineage_keys that are prerequisites of this new asset
  dependents: string[]; // lineage_keys that depend on this new asset
}

const EMPTY_ADD: StagedAdd = {
  lineage_key: "",
  kind: "lesson_plan",
  content: "",
  week_index: 1,
  section: "",
  order: 0,
  source_url: "",
  prereqs: [],
  dependents: [],
};

export function AuthorChange() {
  const { role } = useAuth();
  const canExecute = role != null && AUTHOR_ROLES.has(role);
  const queryClient = useQueryClient();

  // ----- curriculum picker (Course/Graph pattern) -----
  const [selectedId, setSelectedId] = useState<string>("");
  const { data: dashboard, isLoading: dashLoading } = useQuery({
    queryKey: ["dashboard"],
    queryFn: getDashboard,
  });
  const curricula = dashboard?.curricula ?? [];
  const defaultId =
    curricula.find((c) => c.current_version_id)?.id ?? curricula[0]?.id ?? "";
  const curriculumId = selectedId || defaultId;

  const { data: calendar, isLoading: calLoading } = useQuery({
    queryKey: ["course-calendar", curriculumId],
    queryFn: () => getCourseCalendar(curriculumId),
    enabled: !!curriculumId,
  });

  // Flatten calendar tiles → the pool of existing assets used for the "edit"
  // picker, prereq/dependent multi-selects, and placement suggestions.
  const tiles: CalendarTile[] = useMemo(
    () => (calendar?.sections ?? []).flatMap((s) => s.tiles),
    [calendar]
  );
  const placements = useMemo(() => {
    const seen = new Map<string, { week_index: number; section: string }>();
    for (const s of calendar?.sections ?? []) {
      const key = `${s.week_index}::${s.section}`;
      if (!seen.has(key))
        seen.set(key, { week_index: s.week_index, section: s.section });
    }
    return [...seen.values()].sort((a, b) => a.week_index - b.week_index);
  }, [calendar]);

  // ----- staged change-set -----
  const [bump, setBump] = useState<BumpType>("minor");
  const [note, setNote] = useState("");
  const [title, setTitle] = useState("");
  const [changedRows, setChangedRows] = useState<ChangedRow[]>([]);
  const [stagedAdds, setStagedAdds] = useState<StagedAdd[]>([]);

  // ----- "add new asset" sub-form -----
  const [draft, setDraft] = useState<StagedAdd>(EMPTY_ADD);
  const [placementMode, setPlacementMode] = useState<"existing" | "new">(
    "existing"
  );
  const [placementKey, setPlacementKey] = useState<string>(""); // "week::section"

  // tiles not already staged for editing
  const existingForEdit = useMemo(
    () =>
      tiles.filter(
        (t) => !changedRows.some((r) => r.lineage_key === t.lineage_key)
      ),
    [tiles, changedRows]
  );

  // ---- changed[] handlers ----
  function addChangedRow(tile: CalendarTile | null) {
    if (!tile) return;
    if (changedRows.some((r) => r.lineage_key === tile.lineage_key)) return;
    setChangedRows((prev) => [
      ...prev,
      {
        lineage_key: tile.lineage_key,
        label: tile.label,
        kind: tile.kind,
        content: "",
      },
    ]);
  }
  function updateChangedContent(lineageKey: string, content: string) {
    setChangedRows((prev) =>
      prev.map((r) => (r.lineage_key === lineageKey ? { ...r, content } : r))
    );
  }
  function removeChangedRow(lineageKey: string) {
    setChangedRows((prev) => prev.filter((r) => r.lineage_key !== lineageKey));
  }

  // ---- added[] handlers ----
  function applyPlacement(key: string) {
    setPlacementKey(key);
    const p = placements.find((x) => `${x.week_index}::${x.section}` === key);
    if (p)
      setDraft((d) => ({ ...d, week_index: p.week_index, section: p.section }));
  }

  const draftKeyTrimmed = draft.lineage_key.trim();
  const draftKeyConflict =
    draftKeyTrimmed.length > 0 &&
    (tiles.some((t) => t.lineage_key === draftKeyTrimmed) ||
      stagedAdds.some((a) => a.lineage_key === draftKeyTrimmed));
  const canAddDraft =
    draftKeyTrimmed.length > 0 &&
    !draftKeyConflict &&
    draft.section.trim().length > 0 &&
    draft.week_index >= 0;

  function stageAdd() {
    if (!canAddDraft) return;
    setStagedAdds((prev) => [
      ...prev,
      {
        ...draft,
        lineage_key: draftKeyTrimmed,
        section: draft.section.trim(),
        source_url: draft.source_url.trim(),
      },
    ]);
    setDraft(EMPTY_ADD);
    setPlacementMode("existing");
    setPlacementKey("");
  }
  function removeStagedAdd(lineageKey: string) {
    setStagedAdds((prev) => prev.filter((a) => a.lineage_key !== lineageKey));
  }

  // ---- compile the change-set ----
  const changed: ReleaseChangedItem[] = changedRows
    .filter((r) => r.content.trim().length > 0)
    .map((r) => ({ lineage_key: r.lineage_key, content: r.content }));

  const added: ReleaseAddedItem[] = stagedAdds.map((a) => {
    const item: ReleaseAddedItem = {
      lineage_key: a.lineage_key,
      kind: a.kind,
      week_index: a.week_index,
      section: a.section,
      order: a.order,
    };
    if (a.content.trim()) item.content = a.content;
    if (a.source_url.trim()) item.source_url = a.source_url.trim();
    return item;
  });

  const edges_added: ReleaseEdge[] = stagedAdds.flatMap((a) => [
    // prereq → new asset (prereq is the "from"/prerequisite)
    ...a.prereqs.map((p) => ({ from_key: p, to_key: a.lineage_key })),
    // new asset → dependent (new asset becomes prerequisite for dependent)
    ...a.dependents.map((d) => ({ from_key: a.lineage_key, to_key: d })),
  ]);

  const totalEdges = edges_added.length;
  const hasContent =
    changed.length > 0 || added.length > 0 || totalEdges > 0;

  const mutation = useMutation({
    mutationFn: (body: ReleaseCreate) => createRelease(curriculumId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["course-calendar"] });
      queryClient.invalidateQueries({ queryKey: ["graph"] });
    },
  });
  const released: ReleaseResponse | undefined = mutation.data;

  // The structured change-set replayed by both "Execute release" and the
  // PR-style "Submit for review" → merge path (same payload, different verb).
  const changeSet: ReleaseChangeSet = {
    bump,
    changed,
    added,
    removed: [],
    edges_added,
    edges_removed: [],
  };

  // A default title derived from the change summary (overridable in the field).
  const defaultTitle = useMemo(() => {
    const parts: string[] = [];
    if (added.length) parts.push(`add ${added.length}`);
    if (changed.length) parts.push(`update ${changed.length}`);
    if (totalEdges) parts.push(`${totalEdges} edge${totalEdges === 1 ? "" : "s"}`);
    return parts.length ? `Change-set: ${parts.join(", ")}` : "Proposed change-set";
  }, [added.length, changed.length, totalEdges]);

  // affected_kinds = the distinct asset kinds touched by the change-set's adds.
  const affectedKinds = useMemo(
    () => [...new Set(added.map((a) => a.kind))] as AssetKind[],
    [added]
  );

  const submitMutation = useMutation({
    mutationFn: () =>
      createCCR({
        curriculum_id: curriculumId,
        title: title.trim() || defaultTitle,
        proposed_bump: bump,
        affected_kinds: affectedKinds,
        instructor_override: true,
        change_set: changeSet,
        ...(note.trim() ? { rationale: note.trim() } : {}),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ccrs"] });
    },
  });
  const submittedCCR: CCROut | undefined = submitMutation.data;

  function handleSubmitForReview() {
    if (!hasContent || submitMutation.isPending) return;
    submitMutation.mutate();
  }

  // ----- AI impact preview (stateless: no ccr_id persisted) -----
  const impactMutation = useMutation({
    mutationFn: () =>
      analyzeImpact(curriculumId, {
        change_set: changeSet,
        ...(title.trim() ? { title: title.trim() } : {}),
        ...(note.trim() ? { rationale: note.trim() } : {}),
      }),
  });
  const impact: ImpactReport | undefined = impactMutation.data;
  const impactIsUnconfigured =
    impactMutation.error instanceof AxiosError &&
    impactMutation.error.response?.status === 503;

  function handleAnalyzeImpact() {
    if (!hasContent || impactMutation.isPending) return;
    impactMutation.mutate();
  }

  function handleExecute() {
    if (!canExecute || !hasContent || mutation.isPending) return;
    const body: ReleaseCreate = {
      bump,
      changed,
      added,
      removed: [],
      edges_added,
      edges_removed: [],
      // Omit expected_active_id (→ null = "current active") rather than pinning
      // the dashboard's possibly-stale current_version_id, which would trip a
      // false 409. The backend still resolves the true active version; genuine
      // validation errors (422 cycle/dangling/placement) remain fail-closed.
      expected_active_id: null,
    };
    if (note.trim()) body.note = note.trim();
    mutation.mutate(body);
  }

  function resetForNext() {
    mutation.reset();
    submitMutation.reset();
    impactMutation.reset();
    setChangedRows([]);
    setStagedAdds([]);
    setNote("");
    setTitle("");
  }

  // ----- render guards -----
  if (dashLoading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", mt: 8 }}>
        <CircularProgress />
      </Box>
    );
  }
  if (curricula.length === 0) {
    return (
      <Alert severity="warning">
        No curricula found — create one before authoring a release.
      </Alert>
    );
  }

  return (
    <Box sx={{ maxWidth: 1180 }}>
      <Typography variant="h5" gutterBottom>
        Propose Change
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Compose a structured change-set — edit existing assets, add new ones with
        their place in the calendar and dependency edges — then execute it as a
        release to produce a new active version.
      </Typography>

      {/* Curriculum picker */}
      {curricula.length > 1 && (
        <FormControl size="small" sx={{ mb: 2.5, minWidth: 280 }}>
          <InputLabel id="propose-curriculum-label">Curriculum</InputLabel>
          <Select
            labelId="propose-curriculum-label"
            value={curriculumId}
            label="Curriculum"
            data-testid="curriculum-select"
            onChange={(e) => {
              setSelectedId(e.target.value);
              resetForNext();
            }}
          >
            {curricula.map((c) => (
              <MenuItem key={c.id} value={c.id}>
                {c.name}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      )}

      {calLoading && (
        <Box sx={{ display: "flex", justifyContent: "center", my: 3 }}>
          <CircularProgress />
        </Box>
      )}

      {/* Success state */}
      {released && (
        <Alert
          severity="success"
          data-testid="release-success"
          sx={{ mb: 2.5 }}
          action={
            <Button color="inherit" size="small" onClick={resetForNext}>
              Author another
            </Button>
          }
        >
          <AlertTitle>
            Released {released.semver} ({released.status})
          </AlertTitle>
          New active version with {released.member_count} assets and{" "}
          {released.edge_count} edges. Changed {released.summary.changed}, added{" "}
          {released.summary.added}, edges +{released.summary.edges_added}.{" "}
          <Link component={RouterLink} to="/course">
            View course
          </Link>{" "}
          ·{" "}
          <Link component={RouterLink} to="/graph">
            View graph
          </Link>
        </Alert>
      )}

      {/* Submitted-for-review state */}
      {submittedCCR && (
        <Alert
          severity="success"
          data-testid="submit-review-success"
          sx={{ mb: 2.5 }}
          action={
            <Button color="inherit" size="small" onClick={resetForNext}>
              Author another
            </Button>
          }
        >
          <AlertTitle>Submitted for review</AlertTitle>
          “{submittedCCR.title}” is now {submittedCCR.status}. Reviewers can
          approve it and a maintainer can merge it.{" "}
          <Link component={RouterLink} to="/review" data-testid="goto-review-link">
            Go to Review
          </Link>
        </Alert>
      )}

      {/* Two-column row: the short "Change existing" card sits beside the tall "Add new"
          card, keeping the page short enough to stay legible at a mild demo zoom. */}
      <Box
        sx={{
          display: "flex",
          gap: 2.5,
          alignItems: "flex-start",
          flexDirection: { xs: "column", md: "row" },
          mb: 2.5,
        }}
      >
        {/* ---------- Edit existing assets ---------- */}
        <Box sx={{ flex: "1 1 0", minWidth: 0, width: "100%" }}>
          <Card variant="outlined">
            <CardContent>
              <Typography variant="subtitle1" sx={{ fontWeight: 700, mb: 0.5 }}>
                Change existing assets
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
            Pick an asset from the active version and provide new content.
          </Typography>

          <Autocomplete<CalendarTile>
            options={existingForEdit}
            getOptionLabel={(o) => `${o.label} · ${kindLabel(o.kind)}`}
            groupBy={(o) => kindLabel(o.kind)}
            value={null}
            blurOnSelect
            onChange={(_e, val) => addChangedRow(val)}
            isOptionEqualToValue={(o, v) => o.lineage_key === v.lineage_key}
            renderInput={(params) => (
              <TextField
                {...params}
                label="Add asset to change"
                placeholder="Search by name… (filter by kind groups)"
                data-testid="edit-asset-picker"
              />
            )}
          />

          <Stack spacing={1.5} sx={{ mt: 1.5 }}>
            {changedRows.map((row) => (
              <Box
                key={row.lineage_key}
                data-testid="changed-row"
                sx={{
                  p: 1.5,
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 2,
                }}
              >
                <Stack
                  direction="row"
                  sx={{ alignItems: "center", gap: 1, mb: 1 }}
                >
                  <Chip
                    label={kindLabel(row.kind)}
                    size="small"
                    variant="outlined"
                  />
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {row.label}
                  </Typography>
                  <Box sx={{ flexGrow: 1 }} />
                  <IconButton
                    size="small"
                    aria-label={`Remove ${row.label}`}
                    onClick={() => removeChangedRow(row.lineage_key)}
                  >
                    <DeleteOutlineIcon fontSize="small" />
                  </IconButton>
                </Stack>
                <TextField
                  label="New content"
                  fullWidth
                  multiline
                  minRows={2}
                  value={row.content}
                  onChange={(e) =>
                    updateChangedContent(row.lineage_key, e.target.value)
                  }
                />
              </Box>
            ))}
          </Stack>
            </CardContent>
          </Card>
        </Box>
        {/* ---------- Add new assets ---------- */}
        <Box sx={{ flex: "1.3 1 0", minWidth: 0, width: "100%" }}>
          <Card variant="outlined">
            <CardContent>
              <Typography variant="subtitle1" sx={{ fontWeight: 700, mb: 0.5 }}>
                Add new assets
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
            Classify where the asset fits and declare its dependency edges so it
            lands correctly in the graph.
          </Typography>

          <Stack spacing={2}>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
              <TextField
                label="Lineage key"
                required
                fullWidth
                value={draft.lineage_key}
                error={draftKeyConflict}
                helperText={
                  draftKeyConflict
                    ? "Key already exists in this version or staged list."
                    : "A stable unique id, e.g. week3.new-lab"
                }
                onChange={(e) =>
                  setDraft((d) => ({ ...d, lineage_key: e.target.value }))
                }
                data-testid="add-lineage-key"
              />
              <FormControl fullWidth>
                <InputLabel id="add-kind-label">Kind</InputLabel>
                <Select
                  labelId="add-kind-label"
                  label="Kind"
                  value={draft.kind}
                  data-testid="add-kind"
                  onChange={(e) =>
                    setDraft((d) => ({
                      ...d,
                      kind: e.target.value as AssetKind,
                    }))
                  }
                >
                  {ASSET_KINDS.map((k) => (
                    <MenuItem key={k} value={k}>
                      {ASSET_KIND_LABELS[k]}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            </Stack>

            <TextField
              label="Content"
              fullWidth
              multiline
              minRows={3}
              value={draft.content}
              onChange={(e) =>
                setDraft((d) => ({ ...d, content: e.target.value }))
              }
              data-testid="add-content"
            />

            {/* Placement */}
            <Box>
              <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
                Placement
              </Typography>
              <RadioGroup
                row
                value={placementMode}
                onChange={(e) => {
                  const mode = e.target.value as "existing" | "new";
                  setPlacementMode(mode);
                  if (mode === "new") setPlacementKey("");
                }}
              >
                <FormControlLabel
                  value="existing"
                  control={<Radio size="small" />}
                  label="Existing week / section"
                  disabled={placements.length === 0}
                />
                <FormControlLabel
                  value="new"
                  control={<Radio size="small" />}
                  label="New week / section"
                />
              </RadioGroup>

              {placementMode === "existing" ? (
                <FormControl fullWidth size="small" sx={{ mt: 0.5 }}>
                  <InputLabel id="placement-label">
                    Week / section
                  </InputLabel>
                  <Select
                    labelId="placement-label"
                    label="Week / section"
                    value={placementKey}
                    data-testid="placement-existing"
                    onChange={(e) => applyPlacement(e.target.value)}
                  >
                    {placements.map((p) => {
                      const k = `${p.week_index}::${p.section}`;
                      return (
                        <MenuItem key={k} value={k}>
                          {p.week_index > 0
                            ? `Week ${p.week_index}`
                            : "Projects"}{" "}
                          — {p.section}
                        </MenuItem>
                      );
                    })}
                  </Select>
                </FormControl>
              ) : (
                <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
                  <TextField
                    label="Week #"
                    type="number"
                    size="small"
                    sx={{ width: { sm: 140 } }}
                    value={draft.week_index}
                    onChange={(e) =>
                      setDraft((d) => ({
                        ...d,
                        week_index: Number(e.target.value),
                      }))
                    }
                    data-testid="placement-week"
                  />
                  <TextField
                    label="Section name"
                    size="small"
                    fullWidth
                    value={draft.section}
                    onChange={(e) =>
                      setDraft((d) => ({ ...d, section: e.target.value }))
                    }
                    data-testid="placement-section"
                  />
                </Stack>
              )}
            </Box>

            <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
              <TextField
                label="Order"
                type="number"
                size="small"
                sx={{ width: { sm: 140 } }}
                value={draft.order}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, order: Number(e.target.value) }))
                }
              />
              <TextField
                label="Source URL (optional)"
                size="small"
                fullWidth
                value={draft.source_url}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, source_url: e.target.value }))
                }
              />
            </Stack>

            {/* Dependency edges */}
            <Autocomplete<CalendarTile, true>
              multiple
              options={tiles}
              getOptionLabel={(o) => `${o.label} · ${kindLabel(o.kind)}`}
              isOptionEqualToValue={(o, v) => o.lineage_key === v.lineage_key}
              value={tiles.filter((t) =>
                draft.prereqs.includes(t.lineage_key)
              )}
              onChange={(_e, vals) =>
                setDraft((d) => ({
                  ...d,
                  prereqs: vals.map((v) => v.lineage_key),
                }))
              }
              renderInput={(params) => (
                <TextField
                  {...params}
                  label="Prerequisites"
                  placeholder="Assets this depends on"
                  data-testid="add-prereqs"
                />
              )}
            />
            <Autocomplete<CalendarTile, true>
              multiple
              options={tiles}
              getOptionLabel={(o) => `${o.label} · ${kindLabel(o.kind)}`}
              isOptionEqualToValue={(o, v) => o.lineage_key === v.lineage_key}
              value={tiles.filter((t) =>
                draft.dependents.includes(t.lineage_key)
              )}
              onChange={(_e, vals) =>
                setDraft((d) => ({
                  ...d,
                  dependents: vals.map((v) => v.lineage_key),
                }))
              }
              renderInput={(params) => (
                <TextField
                  {...params}
                  label="Becomes a prerequisite for"
                  placeholder="Assets that will depend on this"
                  data-testid="add-dependents"
                />
              )}
            />

            <Box>
              <Button
                variant="outlined"
                onClick={stageAdd}
                disabled={!canAddDraft}
                data-testid="stage-add-btn"
              >
                Add asset to change-set
              </Button>
            </Box>
          </Stack>

          {/* Staged new assets */}
          {stagedAdds.length > 0 && (
            <Stack spacing={1} sx={{ mt: 2 }}>
              {stagedAdds.map((a) => (
                <Box
                  key={a.lineage_key}
                  data-testid="staged-add"
                  sx={{
                    p: 1.25,
                    border: "1px solid",
                    borderColor: "secondary.main",
                    borderRadius: 2,
                  }}
                >
                  <Stack
                    direction="row"
                    sx={{ alignItems: "center", gap: 1, flexWrap: "wrap" }}
                  >
                    <Chip
                      label={kindLabel(a.kind)}
                      size="small"
                      color="secondary"
                      variant="outlined"
                    />
                    <Typography
                      variant="body2"
                      sx={{ fontWeight: 600, fontFamily: "monospace" }}
                    >
                      {a.lineage_key}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {a.week_index > 0 ? `Week ${a.week_index}` : "Projects"} ·{" "}
                      {a.section}
                    </Typography>
                    {a.prereqs.length > 0 && (
                      <Chip
                        label={`${a.prereqs.length} prereq${a.prereqs.length === 1 ? "" : "s"}`}
                        size="small"
                      />
                    )}
                    {a.dependents.length > 0 && (
                      <Chip
                        label={`${a.dependents.length} dependent${a.dependents.length === 1 ? "" : "s"}`}
                        size="small"
                      />
                    )}
                    <Box sx={{ flexGrow: 1 }} />
                    <IconButton
                      size="small"
                      aria-label={`Remove ${a.lineage_key}`}
                      onClick={() => removeStagedAdd(a.lineage_key)}
                    >
                      <DeleteOutlineIcon fontSize="small" />
                    </IconButton>
                  </Stack>
                </Box>
              ))}
            </Stack>
          )}
            </CardContent>
          </Card>
        </Box>
      </Box>

      {/* ---------- Execute strip ---------- */}
      <Card variant="outlined">
        <CardContent>
          <Stack
            direction={{ xs: "column", md: "row" }}
            spacing={2}
            sx={{ alignItems: { md: "center" } }}
          >
            <FormControl size="small" sx={{ minWidth: 140 }}>
              <InputLabel id="bump-label">Bump</InputLabel>
              <Select
                labelId="bump-label"
                label="Bump"
                value={bump}
                data-testid="bump-select"
                onChange={(e) => setBump(e.target.value as BumpType)}
              >
                {BUMPS.map((b) => (
                  <MenuItem key={b} value={b}>
                    {b}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>

            <TextField
              label="Title (for review)"
              size="small"
              fullWidth
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={defaultTitle}
              data-testid="ccr-title"
            />

            <TextField
              label="Note / rationale (optional)"
              size="small"
              fullWidth
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />

            {/* Live review summary */}
            <Stack
              direction="row"
              spacing={1}
              data-testid="review-summary"
              sx={{ flexShrink: 0 }}
            >
              <Chip label={`changed ${changed.length}`} size="small" />
              <Chip label={`added ${added.length}`} size="small" />
              <Chip label={`edges ${totalEdges}`} size="small" />
            </Stack>
          </Stack>

          <Divider sx={{ my: 2 }} />

          {mutation.isError && (
            <Alert severity="error" sx={{ mb: 2 }} data-testid="release-error">
              {extractApiError(mutation.error)}
            </Alert>
          )}
          {submitMutation.isError && (
            <Alert severity="error" sx={{ mb: 2 }} data-testid="submit-review-error">
              {extractApiError(submitMutation.error)}
            </Alert>
          )}

          <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
            <Tooltip
              title={
                !hasContent ? "Stage at least one change before submitting." : ""
              }
              disableHoverListener={hasContent}
            >
              <Box sx={{ display: "inline-block" }}>
                <Button
                  variant="outlined"
                  size="large"
                  onClick={handleSubmitForReview}
                  disabled={!hasContent || submitMutation.isPending}
                  data-testid="submit-review-btn"
                >
                  {submitMutation.isPending
                    ? "Submitting…"
                    : "Submit for review"}
                </Button>
              </Box>
            </Tooltip>

            <Tooltip
              title={
                !hasContent ? "Stage at least one change before analyzing." : ""
              }
              disableHoverListener={hasContent}
            >
              <Box sx={{ display: "inline-block" }}>
                <Button
                  variant="outlined"
                  color="secondary"
                  size="large"
                  onClick={handleAnalyzeImpact}
                  disabled={!hasContent || impactMutation.isPending}
                  data-testid="analyze-impact"
                  startIcon={
                    impactMutation.isPending ? (
                      <CircularProgress size={16} color="inherit" />
                    ) : (
                      <AutoAwesomeIcon />
                    )
                  }
                >
                  {impactMutation.isPending
                    ? "Analyzing…"
                    : "Analyze impact (AI)"}
                </Button>
              </Box>
            </Tooltip>

            <Tooltip
              title={
                !canExecute
                  ? `Your role (${role ?? "unknown"}) cannot execute releases.`
                  : !hasContent
                    ? "Stage at least one change before executing."
                    : ""
              }
              disableHoverListener={canExecute && hasContent}
            >
              <Box sx={{ display: "inline-block" }}>
                <Button
                  variant="contained"
                  size="large"
                  onClick={handleExecute}
                  disabled={!canExecute || !hasContent || mutation.isPending}
                  data-testid="execute-btn"
                >
                  {mutation.isPending
                    ? "Executing…"
                    : "Execute release"}
                </Button>
              </Box>
            </Tooltip>
          </Stack>

          {/* ---------- AI impact panel ---------- */}
          {impactIsUnconfigured && (
            <Alert
              severity="info"
              sx={{ mt: 2 }}
              data-testid="impact-unconfigured"
              onClose={() => impactMutation.reset()}
            >
              AI impact analysis isn't configured on this server.
            </Alert>
          )}
          {impactMutation.isError && !impactIsUnconfigured && (
            <Alert
              severity="error"
              sx={{ mt: 2 }}
              data-testid="impact-error"
              onClose={() => impactMutation.reset()}
            >
              {extractApiError(impactMutation.error)}
            </Alert>
          )}
          {impact && <ImpactPanel report={impact} />}
        </CardContent>
      </Card>
    </Box>
  );
}

/** Renders Claude's advisory impact estimate for the staged change-set. */
function ImpactPanel({ report }: { report: ImpactReport }) {
  const dur = durationChip(report.duration_delta_minutes);
  const loadColor = COGNITIVE_LOAD_COLOR[report.cognitive_load] ?? "default";
  const loadLabel =
    COGNITIVE_LOAD_LABEL[report.cognitive_load] ?? report.cognitive_load;

  return (
    <Card
      variant="outlined"
      data-testid="impact-panel"
      sx={{
        mt: 2,
        borderColor: "secondary.main",
        bgcolor: "action.hover",
      }}
    >
      <CardContent>
        <Stack
          direction="row"
          spacing={1}
          sx={{ alignItems: "center", mb: 1 }}
        >
          <AutoAwesomeIcon fontSize="small" color="secondary" />
          <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
            AI impact analysis
          </Typography>
        </Stack>

        <Typography variant="body1" sx={{ mb: 2 }}>
          {report.summary}
        </Typography>

        {/* Learning objectives */}
        <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
          Learning objectives
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          {report.learning_objectives_impact}
        </Typography>
        {report.affected_objectives.length > 0 && (
          <Box
            component="ul"
            data-testid="impact-objectives"
            sx={{ mt: 0, mb: 2, pl: 3 }}
          >
            {report.affected_objectives.map((o, i) => (
              <Typography component="li" variant="body2" key={i}>
                {o}
              </Typography>
            ))}
          </Box>
        )}

        <Divider sx={{ my: 1.5 }} />

        {/* Duration */}
        <Stack
          direction="row"
          spacing={1}
          sx={{ alignItems: "center", mb: 0.5 }}
        >
          <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
            Duration
          </Typography>
          <Chip
            label={dur.label}
            color={dur.color}
            size="small"
            data-testid="impact-duration-chip"
          />
        </Stack>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
          {report.duration_rationale}
        </Typography>

        {/* Cognitive load */}
        <Stack
          direction="row"
          spacing={1}
          sx={{ alignItems: "center", mb: 0.5 }}
        >
          <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
            Cognitive load
          </Typography>
          <Chip
            label={loadLabel}
            color={loadColor}
            size="small"
            data-testid="impact-load-chip"
          />
        </Stack>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
          {report.cognitive_load_rationale}
        </Typography>

        {/* Risks */}
        {report.risks.length > 0 && (
          <>
            <Divider sx={{ my: 1.5 }} />
            <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
              Risks
            </Typography>
            <Box
              component="ul"
              data-testid="impact-risks"
              sx={{ mt: 0.5, mb: 1.5, pl: 3 }}
            >
              {report.risks.map((r, i) => (
                <Typography component="li" variant="body2" key={i}>
                  {r}
                </Typography>
              ))}
            </Box>
          </>
        )}

        {/* Recommendations */}
        {report.recommendations.length > 0 && (
          <>
            <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
              Recommendations
            </Typography>
            <Box
              component="ul"
              data-testid="impact-recommendations"
              sx={{ mt: 0.5, mb: 1.5, pl: 3 }}
            >
              {report.recommendations.map((r, i) => (
                <Typography component="li" variant="body2" key={i}>
                  {r}
                </Typography>
              ))}
            </Box>
          </>
        )}

        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 1, fontStyle: "italic" }}
        >
          AI-generated, advisory only — a human decides.
        </Typography>
      </CardContent>
    </Card>
  );
}
