/**
 * Review page — the GitHub-PR "review → merge" loop for executable change-sets.
 *
 * Lists CCRs that carry a structured `change_set` (the PR-style submissions from
 * Propose Change). Reviewers expand one to inspect the change-set detail, then:
 *   - Approve  → POST /ccrs/{id}/approvals {decision:"approve"} (records a vote;
 *                release needs ≥2 distinct approvers; you can't approve your own).
 *   - Merge    → POST /ccrs/{id}/merge (architect/PM) → replays the change-set
 *                through fork(), producing a new active version.
 * Server `detail` is surfaced verbatim on 400/409/422 — fail-closed.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link as RouterLink } from "react-router-dom";
import { AxiosError } from "axios";

import Accordion from "@mui/material/Accordion";
import AccordionDetails from "@mui/material/AccordionDetails";
import AccordionSummary from "@mui/material/AccordionSummary";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Divider from "@mui/material/Divider";
import Link from "@mui/material/Link";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import ExpandMoreOutlinedIcon from "@mui/icons-material/ExpandMoreOutlined";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import RadioButtonUncheckedIcon from "@mui/icons-material/RadioButtonUnchecked";

import type {
  CCROut,
  ReleaseChangeSet,
  ReleaseGate,
  ReleaseResponse,
} from "../api/client";
import {
  ASSET_KIND_LABELS,
  approveCCR,
  getCCRGate,
  listCCRs,
  markCCRQaPassed,
  mergeCCR,
} from "../api/client";
import type { AssetKind } from "../api/client";
import { useAuth } from "../auth/AuthContext";

const MERGE_ROLES = new Set(["architect", "program_manager"]);
const QA_ROLES = new Set(["qa_lead", "architect"]);

function kindLabel(kind: string): string {
  return ASSET_KIND_LABELS[kind as AssetKind] ?? kind;
}

/** A one-line summary like "+1 added, 0 changed, +1 edge". */
function summarize(cs: ReleaseChangeSet): string {
  const edges = cs.edges_added.length + cs.edges_removed.length;
  const parts = [
    `+${cs.added.length} added`,
    `${cs.changed.length} changed`,
  ];
  if (cs.removed.length) parts.push(`-${cs.removed.length} removed`);
  parts.push(`+${edges} edge${edges === 1 ? "" : "s"}`);
  return parts.join(", ");
}

function statusColor(
  status: string
): "default" | "success" | "warning" | "info" {
  if (status === "active" || status === "released") return "success";
  if (status === "approved") return "info";
  if (status === "rejected" || status === "archived") return "warning";
  return "default";
}

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
    if (status === 409) {
      return (
        detailStr ??
        "Needs ≥2 approvals from different reviewers (you can't approve your own)."
      );
    }
    if (status === 422) {
      return `Invalid change-set: ${detailStr ?? "dangling edge or cycle."}`;
    }
    if (status === 400) {
      return detailStr ?? "This change request has no executable change-set.";
    }
    if (detailStr) return detailStr;
    return err.message;
  }
  return "Something went wrong. Please try again.";
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString();
}

export function Review() {
  const { role } = useAuth();
  const [expanded, setExpanded] = useState<string | false>(false);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["ccrs"],
    queryFn: () => listCCRs(),
  });

  // Only CCRs carrying an executable change-set are reviewable PRs.
  const ccrs: CCROut[] = (data ?? []).filter((c) => c.change_set != null);

  if (isLoading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", mt: 8 }}>
        <CircularProgress />
      </Box>
    );
  }
  if (isError) {
    return (
      <Alert severity="error">{extractApiError(error)}</Alert>
    );
  }

  return (
    <Box sx={{ maxWidth: 880 }}>
      <Typography variant="h5" gutterBottom>
        Review
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2.5 }}>
        Change requests submitted for review carry an executable change-set.
        Approve one (release needs ≥2 distinct approvers), then a maintainer
        merges it to produce a new active version.
      </Typography>

      {ccrs.length === 0 ? (
        <Alert severity="info" data-testid="review-empty">
          No change requests are open for review. Stage a change-set in{" "}
          <Link component={RouterLink} to="/propose">
            Propose Change
          </Link>{" "}
          and click “Submit for review”.
        </Alert>
      ) : (
        <Stack spacing={1.5} data-testid="review-list">
          {ccrs.map((ccr) => {
            const cs = ccr.change_set as ReleaseChangeSet;
            return (
              <Accordion
                key={ccr.id}
                expanded={expanded === ccr.id}
                onChange={(_e, isExp) => setExpanded(isExp ? ccr.id : false)}
                data-testid="review-item"
                variant="outlined"
                disableGutters
                slotProps={{ transition: { unmountOnExit: true } }}
              >
                <AccordionSummary expandIcon={<ExpandMoreOutlinedIcon />}>
                  <Stack
                    direction="row"
                    spacing={1}
                    sx={{ alignItems: "center", flexWrap: "wrap", width: "100%" }}
                  >
                    <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
                      {ccr.title}
                    </Typography>
                    <Chip
                      label={ccr.status}
                      size="small"
                      color={statusColor(ccr.status)}
                      variant="outlined"
                      data-testid="review-status"
                    />
                    <Box sx={{ flexGrow: 1 }} />
                    <Typography variant="caption" color="text.secondary">
                      {summarize(cs)} · {formatDate(ccr.created_at)}
                    </Typography>
                  </Stack>
                </AccordionSummary>
                <AccordionDetails>
                  <ReviewPanel
                    ccr={ccr}
                    cs={cs}
                    role={role}
                    expanded={expanded === ccr.id}
                  />
                </AccordionDetails>
              </Accordion>
            );
          })}
        </Stack>
      )}
    </Box>
  );
}

/**
 * Expanded body for one reviewable CCR. Owns its own gate query and the
 * approve / QA / merge mutations so each panel's state (gate, pending, alerts)
 * is scoped to its CCR. The gate query only runs while the panel is expanded.
 */
function ReviewPanel({
  ccr,
  cs,
  role,
  expanded,
}: {
  ccr: CCROut;
  cs: ReleaseChangeSet;
  role: string | null;
  expanded: boolean;
}) {
  const queryClient = useQueryClient();
  const canMerge = role != null && MERGE_ROLES.has(role);
  const canQa = role != null && QA_ROLES.has(role);
  const gateKey = ["ccr-gate", ccr.id] as const;

  const gateQuery = useQuery({
    queryKey: gateKey,
    queryFn: () => getCCRGate(ccr.id),
    enabled: expanded,
  });
  const gate = gateQuery.data;

  const invalidateGate = () =>
    queryClient.invalidateQueries({ queryKey: gateKey });

  const approveMutation = useMutation({
    mutationFn: () => approveCCR(ccr.id, "approve"),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ccrs"] });
      invalidateGate();
    },
  });

  const qaMutation = useMutation({
    mutationFn: () => markCCRQaPassed(ccr.id),
    onSuccess: () => {
      invalidateGate();
    },
  });

  const mergeMutation = useMutation({
    mutationFn: () => mergeCCR(ccr.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ccrs"] });
      queryClient.invalidateQueries({ queryKey: ["course-calendar"] });
      queryClient.invalidateQueries({ queryKey: ["graph"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      invalidateGate();
    },
  });

  const merged: ReleaseResponse | undefined = mergeMutation.data;
  const mergeBlocked = gate ? !gate.can_release : true;

  return (
    <>
      <ChangeSetDetail cs={cs} />

      <Divider sx={{ my: 2 }} />

      <GateChecklist
        gate={gate}
        loading={gateQuery.isLoading}
        error={gateQuery.isError ? extractApiError(gateQuery.error) : null}
      />

      <Divider sx={{ my: 2 }} />

      {/* Scoped result alerts for this panel */}
      {approveMutation.isError && (
        <Alert severity="error" sx={{ mb: 2 }} data-testid="approve-error">
          {extractApiError(approveMutation.error)}
        </Alert>
      )}
      {approveMutation.isSuccess && (
        <Alert severity="success" sx={{ mb: 2 }} data-testid="approve-success">
          Approval recorded.
        </Alert>
      )}
      {qaMutation.isError && (
        <Alert severity="error" sx={{ mb: 2 }} data-testid="qa-error">
          {extractApiError(qaMutation.error)}
        </Alert>
      )}
      {qaMutation.isSuccess && (
        <Alert severity="success" sx={{ mb: 2 }} data-testid="qa-success">
          QA review recorded — passed.
        </Alert>
      )}
      {mergeMutation.isError && (
        <Alert severity="error" sx={{ mb: 2 }} data-testid="merge-error">
          {extractApiError(mergeMutation.error)}
        </Alert>
      )}
      {merged && (
        <Alert severity="success" sx={{ mb: 2 }} data-testid="merge-success">
          <AlertTitle>
            Merged — released {merged.semver} ({merged.status})
          </AlertTitle>
          New active version with {merged.member_count} assets and{" "}
          {merged.edge_count} edges.{" "}
          <Link component={RouterLink} to="/course">
            View course
          </Link>{" "}
          ·{" "}
          <Link component={RouterLink} to="/graph">
            View graph
          </Link>
        </Alert>
      )}

      <Stack direction="row" spacing={1.5} sx={{ flexWrap: "wrap", rowGap: 1 }}>
        <Button
          variant="outlined"
          onClick={() => approveMutation.mutate()}
          disabled={approveMutation.isPending}
          data-testid="approve-btn"
        >
          {approveMutation.isPending ? "Approving…" : "Approve"}
        </Button>

        {canQa && (
          <Button
            variant="outlined"
            color="secondary"
            onClick={() => qaMutation.mutate()}
            disabled={qaMutation.isPending || gate?.qa_passed === true}
            data-testid="qa-btn"
          >
            {gate?.qa_passed
              ? "QA passed"
              : qaMutation.isPending
                ? "Submitting QA…"
                : "Mark QA passed"}
          </Button>
        )}

        <Tooltip
          title={
            !canMerge
              ? `Your role (${role ?? "unknown"}) cannot merge.`
              : mergeBlocked
                ? "Needs QA + ≥2 approvals incl. an instructor"
                : ""
          }
          disableHoverListener={canMerge && !mergeBlocked}
        >
          <Box sx={{ display: "inline-block" }}>
            <Button
              variant="contained"
              onClick={() => mergeMutation.mutate()}
              disabled={!canMerge || mergeBlocked || mergeMutation.isPending}
              data-testid="merge-btn"
            >
              {mergeMutation.isPending ? "Merging…" : "Merge"}
            </Button>
          </Box>
        </Tooltip>
      </Stack>
    </>
  );
}

/** Compact checklist of the three merge-gate conditions. Theme-token colors. */
function GateChecklist({
  gate,
  loading,
  error,
}: {
  gate: ReleaseGate | undefined;
  loading: boolean;
  error: string | null;
}) {
  if (loading && !gate) {
    return (
      <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
        <CircularProgress size={16} />
        <Typography variant="body2" color="text.secondary">
          Checking merge gate…
        </Typography>
      </Stack>
    );
  }
  if (error) {
    return (
      <Alert severity="warning" data-testid="gate-error">
        Couldn’t load merge gate: {error}
      </Alert>
    );
  }
  if (!gate) return null;

  return (
    <Stack spacing={0.75} data-testid="gate-checklist">
      <Typography variant="body2" sx={{ fontWeight: 600 }}>
        Merge gate
      </Typography>
      <GateRow
        met={gate.qa_passed}
        label={gate.qa_passed ? "QA review passed" : "QA review needed"}
        testid="gate-qa"
      />
      <GateRow
        met={gate.approval_count >= 2}
        label={`Approvals ${gate.approval_count} (need ≥2)`}
        testid="gate-approvals"
      />
      <GateRow
        met={gate.has_instructor_approval}
        label={
          gate.has_instructor_approval
            ? "Instructor approval ✓"
            : "Instructor approval needed"
        }
        testid="gate-instructor"
      />
    </Stack>
  );
}

function GateRow({
  met,
  label,
  testid,
}: {
  met: boolean;
  label: string;
  testid: string;
}) {
  return (
    <Stack
      direction="row"
      spacing={1}
      sx={{ alignItems: "center" }}
      data-testid={testid}
      data-met={met ? "true" : "false"}
    >
      {met ? (
        <CheckCircleIcon fontSize="small" color="success" />
      ) : (
        <RadioButtonUncheckedIcon
          fontSize="small"
          sx={{ color: "text.secondary" }}
        />
      )}
      <Typography
        variant="body2"
        sx={{ color: met ? "text.primary" : "text.secondary" }}
      >
        {label}
      </Typography>
    </Stack>
  );
}

/** Read-only detail of a change-set: added / changed / removed assets + edges. */
function ChangeSetDetail({ cs }: { cs: ReleaseChangeSet }) {
  return (
    <Stack spacing={1.5}>
      <Chip
        label={`bump: ${cs.bump}`}
        size="small"
        color="primary"
        variant="outlined"
        sx={{ alignSelf: "flex-start" }}
      />

      <Section title={`Added (${cs.added.length})`}>
        {cs.added.map((a) => (
          <Row key={a.lineage_key}>
            <Chip label={kindLabel(a.kind)} size="small" variant="outlined" />
            <Typography variant="body2" sx={{ fontFamily: "monospace" }}>
              {a.lineage_key}
            </Typography>
          </Row>
        ))}
      </Section>

      <Section title={`Changed (${cs.changed.length})`}>
        {cs.changed.map((c) => (
          <Row key={c.lineage_key}>
            <Typography variant="body2" sx={{ fontFamily: "monospace" }}>
              {c.lineage_key}
            </Typography>
          </Row>
        ))}
      </Section>

      {cs.removed.length > 0 && (
        <Section title={`Removed (${cs.removed.length})`}>
          {cs.removed.map((key) => (
            <Row key={key}>
              <Typography variant="body2" sx={{ fontFamily: "monospace" }}>
                {key}
              </Typography>
            </Row>
          ))}
        </Section>
      )}

      <Section
        title={`Edges (+${cs.edges_added.length} / -${cs.edges_removed.length})`}
      >
        {cs.edges_added.map((e, i) => (
          <Row key={`a-${i}`}>
            <Chip label="+" size="small" color="success" variant="outlined" />
            <Typography variant="body2" sx={{ fontFamily: "monospace" }}>
              {e.from_key} → {e.to_key}
            </Typography>
          </Row>
        ))}
        {cs.edges_removed.map((e, i) => (
          <Row key={`r-${i}`}>
            <Chip label="−" size="small" color="warning" variant="outlined" />
            <Typography variant="body2" sx={{ fontFamily: "monospace" }}>
              {e.from_key} → {e.to_key}
            </Typography>
          </Row>
        ))}
      </Section>
    </Stack>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  const hasRows = Array.isArray(children)
    ? children.some(Boolean)
    : Boolean(children);
  return (
    <Box>
      <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
        {title}
      </Typography>
      {hasRows ? (
        <Stack spacing={0.5}>{children}</Stack>
      ) : (
        <Typography variant="caption" color="text.secondary">
          none
        </Typography>
      )}
    </Box>
  );
}

function Row({ children }: { children: React.ReactNode }) {
  return (
    <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
      {children}
    </Stack>
  );
}
