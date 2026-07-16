/**
 * NewCCR page — author a new Change Control Request.
 *
 * POSTs to /api/v1/ccrs via createCCR(). curriculum_id is resolved from the
 * org's curricula (fetched through the dashboard endpoint, mirroring Graph.tsx):
 * if exactly one curriculum exists it is used directly; multiple curricula
 * surface a Select. Authoring is gated to architect / program_manager roles
 * (the API also enforces 403). API errors (403 / 422) are surfaced, not swallowed.
 */

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { AxiosError } from "axios";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Checkbox from "@mui/material/Checkbox";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import FormControl from "@mui/material/FormControl";
import FormHelperText from "@mui/material/FormHelperText";
import IconButton from "@mui/material/IconButton";
import InputLabel from "@mui/material/InputLabel";
import ListItemText from "@mui/material/ListItemText";
import MenuItem from "@mui/material/MenuItem";
import OutlinedInput from "@mui/material/OutlinedInput";
import Select, { type SelectChangeEvent } from "@mui/material/Select";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";

import {
  ASSET_KINDS,
  ASSET_KIND_LABELS,
  createCCR,
  getDashboard,
} from "../api/client";
import type { AssetKind, BumpType, CCRCreate } from "../api/client";
import { useAuth } from "../auth/AuthContext";

const AUTHOR_ROLES = new Set(["architect", "program_manager"]);
const BUMPS: BumpType[] = ["major", "minor", "patch"];

// Plain-language definitions of each asset kind, surfaced via the info
// affordance next to the "Affected kinds" label.
const KIND_DEFINITIONS: { kind: AssetKind; definition: string }[] = [
  { kind: "lesson_plan", definition: "the session plan" },
  { kind: "slides", definition: "presentation deck" },
  { kind: "assessment", definition: "graded assessment" },
  { kind: "rubric", definition: "grading criteria" },
  { kind: "lab", definition: "hands-on coding exercise" },
  { kind: "spec", definition: "the assignment/project specification" },
  {
    kind: "starter",
    definition: "scaffolding/boilerplate students begin from",
  },
  { kind: "references", definition: "supporting material" },
  { kind: "learning_objectives", definition: "the LOs for the unit" },
  { kind: "project", definition: "a multi-week project" },
];

function extractApiError(err: unknown): string {
  if (err instanceof AxiosError) {
    const status = err.response?.status;
    const detail = err.response?.data?.detail;
    const detailStr =
      typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : null;
    if (status === 403) {
      return detailStr ?? "You are not authorized to create change requests.";
    }
    if (status === 422) {
      return `Validation error: ${detailStr ?? "check the form fields."}`;
    }
    if (detailStr) return detailStr;
    return err.message;
  }
  return "Failed to create change request. Please try again.";
}

export function NewCCR() {
  const { role } = useAuth();
  const canAuthor = role != null && AUTHOR_ROLES.has(role);

  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data: dashboardData, isLoading: isDashLoading } = useQuery({
    queryKey: ["dashboard"],
    queryFn: getDashboard,
  });

  const curricula = useMemo(
    () => dashboardData?.curricula ?? [],
    [dashboardData]
  );

  // Form state
  const [curriculumId, setCurriculumId] = useState<string>("");
  const [title, setTitle] = useState("");
  const [rationale, setRationale] = useState("");
  const [proposedBump, setProposedBump] = useState<BumpType>("minor");
  const [affectedKinds, setAffectedKinds] = useState<AssetKind[]>([]);
  const [externalLink, setExternalLink] = useState("");

  // Resolve curriculum_id: explicit pick, else the single curriculum, else "".
  const resolvedCurriculumId =
    curriculumId || (curricula.length === 1 ? curricula[0].id : "");

  const mutation = useMutation({
    mutationFn: (body: CCRCreate) => createCCR(body),
    onSuccess: (ccr) => {
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["ccrs"] });
      navigate("/", { state: { ccrCreatedId: ccr.id } });
    },
  });

  const titleTrimmed = title.trim();
  const canSubmit =
    canAuthor &&
    titleTrimmed.length > 0 &&
    resolvedCurriculumId !== "" &&
    affectedKinds.length > 0 &&
    !mutation.isPending;

  function handleKindsChange(e: SelectChangeEvent<AssetKind[]>) {
    const value = e.target.value;
    setAffectedKinds(
      (typeof value === "string" ? value.split(",") : value) as AssetKind[]
    );
  }

  function handleSubmit() {
    if (!canSubmit) return;
    const body: CCRCreate = {
      curriculum_id: resolvedCurriculumId,
      title: titleTrimmed,
      proposed_bump: proposedBump,
      affected_kinds: affectedKinds,
    };
    if (rationale.trim()) body.rationale = rationale.trim();
    if (externalLink.trim()) body.external_link = externalLink.trim();
    mutation.mutate(body);
  }

  if (!canAuthor) {
    return (
      <Alert severity="warning">
        Your role ({role ?? "unknown"}) is not permitted to author change
        requests. Contact an architect or program manager.
      </Alert>
    );
  }

  if (isDashLoading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", mt: 8 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (curricula.length === 0) {
    return (
      <Alert severity="warning">
        No curricula found — create one before authoring a change request.
      </Alert>
    );
  }

  return (
    <Box>
      <Typography variant="h5" gutterBottom>
        New Change Request
      </Typography>

      <Card variant="outlined" sx={{ maxWidth: 720 }}>
        <CardContent>
          <Stack spacing={2.5}>
            {curricula.length > 1 && (
              <FormControl fullWidth required>
                <InputLabel id="ccr-curriculum-label">Curriculum</InputLabel>
                <Select
                  labelId="ccr-curriculum-label"
                  label="Curriculum"
                  value={curriculumId}
                  onChange={(e) => setCurriculumId(e.target.value)}
                >
                  {curricula.map((c) => (
                    <MenuItem key={c.id} value={c.id}>
                      {c.name}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            )}

            <TextField
              label="Title"
              required
              fullWidth
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />

            <TextField
              label="Rationale"
              fullWidth
              multiline
              minRows={3}
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
            />

            <FormControl fullWidth>
              <InputLabel id="ccr-bump-label">Proposed bump</InputLabel>
              <Select
                labelId="ccr-bump-label"
                label="Proposed bump"
                value={proposedBump}
                onChange={(e) => setProposedBump(e.target.value as BumpType)}
              >
                {BUMPS.map((b) => (
                  <MenuItem key={b} value={b}>
                    {b}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>

            <FormControl fullWidth required error={affectedKinds.length === 0}>
              <Stack
                direction="row"
                spacing={0.5}
                sx={{ alignItems: "center", mb: 0.5 }}
              >
                <Typography variant="body2" component="span" sx={{ fontWeight: 600 }}>
                  Affected kinds
                </Typography>
                <Tooltip
                  arrow
                  title={
                    <Box sx={{ py: 0.5 }}>
                      {KIND_DEFINITIONS.map((d) => (
                        <Typography
                          key={d.kind}
                          variant="caption"
                          component="div"
                          sx={{ mb: 0.25 }}
                        >
                          <strong>{ASSET_KIND_LABELS[d.kind]}</strong> —{" "}
                          {d.definition}
                        </Typography>
                      ))}
                    </Box>
                  }
                >
                  <IconButton
                    size="small"
                    aria-label="Affected kind definitions"
                    sx={{ p: 0.25 }}
                  >
                    <InfoOutlinedIcon fontSize="inherit" />
                  </IconButton>
                </Tooltip>
              </Stack>
              <Select<AssetKind[]>
                multiple
                displayEmpty
                value={affectedKinds}
                onChange={handleKindsChange}
                input={<OutlinedInput />}
                inputProps={{ "aria-label": "Affected kinds" }}
                renderValue={(selected) => (
                  <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5 }}>
                    {selected.map((k) => (
                      <Chip key={k} label={ASSET_KIND_LABELS[k] ?? k} size="small" />
                    ))}
                  </Box>
                )}
              >
                {ASSET_KINDS.map((k) => (
                  <MenuItem key={k} value={k}>
                    <Checkbox checked={affectedKinds.indexOf(k) > -1} />
                    <ListItemText primary={ASSET_KIND_LABELS[k] ?? k} />
                  </MenuItem>
                ))}
              </Select>
              <FormHelperText>Select at least one affected kind</FormHelperText>
            </FormControl>

            <TextField
              label="External link"
              fullWidth
              placeholder="https://jira.example.com/browse/CUR-123"
              value={externalLink}
              onChange={(e) => setExternalLink(e.target.value)}
            />

            {mutation.isError && (
              <Alert severity="error">{extractApiError(mutation.error)}</Alert>
            )}

            <Box>
              <Button
                variant="contained"
                onClick={handleSubmit}
                disabled={!canSubmit}
              >
                {mutation.isPending ? "Submitting…" : "Submit change request"}
              </Button>
            </Box>
          </Stack>
        </CardContent>
      </Card>
    </Box>
  );
}
