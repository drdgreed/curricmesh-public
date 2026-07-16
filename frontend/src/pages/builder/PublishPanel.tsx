/**
 * PublishPanel — a draft-course summary (objective/item counts, total student
 * hours, # overload weeks) and the mandatory QA → release surface (slice 5).
 *
 * Publishing NO LONGER activates a course. "Submit for review" assembles a
 * pre-active candidate version + opens an initial-release ChangeRequest; the
 * course becomes live only after it clears the 6-dimension QA + approval gate.
 * This panel surfaces that gate (QA pass · ≥2 approvals · ≥1 instructor) and the
 * actions to satisfy it, then the Release action that activates the course.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link as RouterLink } from "react-router-dom";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Divider from "@mui/material/Divider";
import Link from "@mui/material/Link";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import type { CourseOut, PublishResponse } from "../../api/builder";
import {
  getEffort,
  getOverload,
  listItems,
  listObjectives,
  publishCourse,
} from "../../api/builder";
import type { CCROut } from "../../api/client";
import {
  approveCCR,
  getCCRGate,
  markCCRQaPassed,
  releaseCCR,
} from "../../api/client";

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <Box sx={{ display: "flex", justifyContent: "space-between" }}>
      <Typography variant="body2" color="text.secondary">
        {label}
      </Typography>
      <Typography variant="body2" sx={{ fontWeight: 600 }}>
        {value}
      </Typography>
    </Box>
  );
}

function GateRow({ ok, label }: { ok: boolean; label: string }) {
  return (
    <Box sx={{ display: "flex", justifyContent: "space-between" }}>
      <Typography variant="body2" color="text.secondary">
        {label}
      </Typography>
      <Typography
        variant="body2"
        sx={{ fontWeight: 600 }}
        color={ok ? "success.main" : "text.disabled"}
      >
        {ok ? "✓" : "—"}
      </Typography>
    </Box>
  );
}

export function PublishPanel({ course }: { course: CourseOut }) {
  const queryClient = useQueryClient();
  const courseId = course.id;

  const objectivesQuery = useQuery({
    queryKey: ["builder-objectives", courseId],
    queryFn: () => listObjectives(courseId),
  });
  const itemsQuery = useQuery({
    queryKey: ["builder-items", courseId],
    queryFn: () => listItems(courseId),
  });
  const effortQuery = useQuery({
    queryKey: ["builder-effort", courseId],
    queryFn: () => getEffort(courseId),
  });
  const overloadQuery = useQuery({
    queryKey: ["builder-overload", courseId],
    queryFn: () => getOverload(courseId),
  });

  const publishMutation = useMutation<PublishResponse>({
    mutationFn: () => publishCourse(courseId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["builder-course", courseId] });
    },
  });

  const published = publishMutation.data ?? null;
  const ccrId = published?.ccr_id ?? null;

  // Poll the release gate once we have a candidate CCR.
  const gateQuery = useQuery({
    queryKey: ["ccr-gate", ccrId],
    queryFn: () => getCCRGate(ccrId as string),
    enabled: !!ccrId,
    refetchOnWindowFocus: false,
  });
  const gate = gateQuery.data ?? null;

  const qaMutation = useMutation({
    mutationFn: () => markCCRQaPassed(ccrId as string),
    onSuccess: () => gateQuery.refetch(),
  });
  const approveMutation = useMutation({
    mutationFn: () => approveCCR(ccrId as string, "approve"),
    onSuccess: () => gateQuery.refetch(),
  });
  const releaseMutation = useMutation<CCROut>({
    mutationFn: () => releaseCCR(ccrId as string),
    onSuccess: () => {
      // The course is now live — refresh the surfaces that list it.
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["course-calendar"] });
      queryClient.invalidateQueries({ queryKey: ["graph"] });
      queryClient.invalidateQueries({ queryKey: ["builder-course", courseId] });
    },
  });
  const released = releaseMutation.data ?? null;

  const objectiveCount = objectivesQuery.data?.length ?? 0;
  const itemCount = itemsQuery.data?.length ?? 0;
  const totalHours = (effortQuery.data?.total_student_minutes ?? 0) / 60;
  const overloadWeeks = (overloadQuery.data ?? []).filter(
    (w) => w.overload
  ).length;

  // A course submitted in a prior session (reload): status is "in_review" /
  // "published" and it already has a curriculum, but we no longer hold its CCR.
  const alreadySubmitted =
    !published &&
    (course.status === "in_review" ||
      course.status === "published" ||
      !!course.curriculum_id);

  return (
    <Paper variant="outlined" sx={{ p: 2.5 }} data-testid="publish-panel">
      <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1.5 }}>
        Publish
      </Typography>

      <Stack spacing={0.75} sx={{ mb: 2 }}>
        <Stat label="Objectives" value={objectiveCount} />
        <Stat label="Items" value={itemCount} />
        <Stat label="Total student hours" value={totalHours.toFixed(1)} />
        <Stat label="Overload weeks" value={overloadWeeks} />
      </Stack>

      <Divider sx={{ mb: 2 }} />

      {released ? (
        <Alert severity="success" data-testid="release-success" sx={{ mb: 1 }}>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            Released {course.title} — v{published?.semver} is now live
          </Typography>
          <Typography variant="body2">
            {published?.member_count} item
            {published?.member_count === 1 ? "" : "s"},{" "}
            {published?.edge_count} edge
            {published?.edge_count === 1 ? "" : "s"}.
          </Typography>
          <Stack direction="row" spacing={2} sx={{ mt: 1 }}>
            <Link component={RouterLink} to="/course" data-testid="goto-course-link">
              View in Course
            </Link>
            <Link component={RouterLink} to="/graph" data-testid="goto-graph-link">
              View graph
            </Link>
          </Stack>
        </Alert>
      ) : published ? (
        // Submitted for review — show the mandatory QA + approval gate.
        <Stack spacing={1.5} data-testid="publish-review-panel">
          <Alert severity="info" sx={{ mb: 0 }}>
            <Typography variant="body2" sx={{ fontWeight: 600 }}>
              Submitted for review — v{published.semver} (candidate)
            </Typography>
            <Typography variant="body2">
              This course is NOT live yet. It must clear QA + approvals before it
              can be released.
            </Typography>
          </Alert>

          <Box>
            <Typography variant="caption" color="text.secondary">
              Release gate
            </Typography>
            <Stack spacing={0.5} sx={{ mt: 0.5 }}>
              <GateRow ok={!!gate?.qa_passed} label="6-dimension QA passed" />
              <GateRow
                ok={(gate?.approval_count ?? 0) >= 2}
                label={`Approvals (${gate?.approval_count ?? 0}/2)`}
              />
              <GateRow
                ok={!!gate?.has_instructor_approval}
                label="Instructor approval"
              />
            </Stack>
          </Box>

          {releaseMutation.isError && (
            <Alert severity="error" sx={{ mb: 0 }}>
              Release failed — the gate is not satisfied yet.
            </Alert>
          )}

          <Stack direction="row" spacing={1}>
            <Button
              size="small"
              variant="outlined"
              onClick={() => qaMutation.mutate()}
              disabled={qaMutation.isPending || !!gate?.qa_passed}
              data-testid="qa-pass-btn"
            >
              Mark QA passed
            </Button>
            <Button
              size="small"
              variant="outlined"
              onClick={() => approveMutation.mutate()}
              disabled={approveMutation.isPending}
              data-testid="approve-btn"
            >
              Approve
            </Button>
          </Stack>

          <Button
            fullWidth
            variant="contained"
            onClick={() => releaseMutation.mutate()}
            disabled={releaseMutation.isPending || !gate?.can_release}
            data-testid="release-btn"
          >
            {releaseMutation.isPending ? "Releasing…" : "Release"}
          </Button>
        </Stack>
      ) : (
        <>
          {publishMutation.isError && (
            <Alert severity="error" sx={{ mb: 1 }}>
              Submit failed. The draft may have a dependency cycle, a dangling
              edge, or already be submitted.
            </Alert>
          )}
          {alreadySubmitted && !publishMutation.isError && (
            <Alert severity="info" sx={{ mb: 1 }} data-testid="already-submitted">
              This course has already been submitted for review. Track its QA +
              approval gate on the Review page.
            </Alert>
          )}
          <Button
            fullWidth
            variant="contained"
            onClick={() => publishMutation.mutate()}
            disabled={
              publishMutation.isPending || itemCount === 0 || alreadySubmitted
            }
            data-testid="publish-btn"
          >
            {publishMutation.isPending ? "Submitting…" : "Submit for review"}
          </Button>
          {itemCount === 0 && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 1 }}
            >
              Add at least one item before submitting.
            </Typography>
          )}
        </>
      )}
    </Paper>
  );
}
