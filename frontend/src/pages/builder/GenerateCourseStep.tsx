/**
 * GenerateCourseStep — "Generate course from a brief" (async).
 *
 * A minimal brief form (title / topic / learner profile / weeks / objectives
 * count) that POSTs to /builder/generate-course. Generation is a long,
 * background job (1 + 2*objectives_count sequential AI calls), so the POST
 * returns a job id immediately; this component then polls the job every ~2.5s,
 * shows a live progress bar, and on completion hands the new draft course id
 * back to the shell (which navigates the author into it). On failure it shows
 * the error and lets the author retry.
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import LinearProgress from "@mui/material/LinearProgress";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import type { LearnerProfile } from "../../api/builder";
import { generateCourse, getGenerationJob } from "../../api/builder";

const EXPERIENCE_LEVELS = ["beginner", "intermediate", "advanced"];
const OBJECTIVES_MAX = 20; // the backend cost bound (CourseBrief: objectives_count <= 20)
const POLL_MS = 2500;

export function GenerateCourseStep({
  onCreated,
}: {
  /** Called with the new draft course id once generation completes. */
  onCreated: (courseId: string) => void;
}) {
  const [title, setTitle] = useState("");
  const [topic, setTopic] = useState("");
  const [experienceLevel, setExperienceLevel] = useState("");
  const [learnerRole, setLearnerRole] = useState("");
  const [targetWeeks, setTargetWeeks] = useState("6");
  const [objectivesCount, setObjectivesCount] = useState("6");
  const [hoursPerWeek, setHoursPerWeek] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);

  const startMutation = useMutation({
    mutationFn: generateCourse,
    onSuccess: (res) => setJobId(res.job_id),
  });

  const jobQuery = useQuery({
    queryKey: ["generation-job", jobId],
    queryFn: () => getGenerationJob(jobId as string),
    enabled: !!jobId,
    // Poll until the job reaches a terminal state, then stop.
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "complete" || status === "failed" ? false : POLL_MS;
    },
  });

  const job = jobQuery.data;

  // Hand the finished draft to the shell exactly once.
  useEffect(() => {
    if (job?.status === "complete" && job.course_id) {
      onCreated(job.course_id);
    }
  }, [job?.status, job?.course_id, onCreated]);

  const weeks = parseInt(targetWeeks, 10);
  const count = parseInt(objectivesCount, 10);
  const countValid =
    !Number.isNaN(count) && count >= 1 && count <= OBJECTIVES_MAX;
  const weeksValid = !Number.isNaN(weeks) && weeks >= 1 && weeks <= 52;
  const canSubmit =
    !!title.trim() && !!topic.trim() && countValid && weeksValid;

  const failed = job?.status === "failed";
  // Active = a job is scheduled and not yet terminal (or we're still POSTing).
  const generating =
    startMutation.isPending ||
    (!!jobId && !failed && job?.status !== "complete");

  function handleSubmit() {
    if (!canSubmit || generating) return;

    const profile: LearnerProfile = {};
    if (experienceLevel) profile.experience_level = experienceLevel;
    if (learnerRole.trim()) profile.role = learnerRole.trim();
    const hours = parseFloat(hoursPerWeek);

    startMutation.mutate({
      title: title.trim(),
      topic: topic.trim(),
      learner_profile: Object.keys(profile).length > 0 ? profile : undefined,
      target_weeks: weeks,
      objectives_count: count,
      hours_per_week: Number.isNaN(hours) ? undefined : hours,
    });
  }

  function handleRetry() {
    setJobId(null);
    startMutation.reset();
  }

  const pct =
    job && job.total_steps > 0
      ? Math.round((job.completed_steps / job.total_steps) * 100)
      : 0;

  return (
    <Paper
      variant="outlined"
      sx={{ p: 3, maxWidth: 640 }}
      data-testid="generate-course-form"
    >
      <Typography variant="h6" gutterBottom>
        Generate course from a brief
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2.5 }}>
        Describe the course and let CurricMesh draft a full first version —
        objectives, lessons, and assessments across your weeks. The result is a{" "}
        <strong>mutable draft</strong> you refine here, then submit through QA
        and release.
      </Typography>

      <Stack spacing={2}>
        <TextField
          label="Title"
          required
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          fullWidth
          size="small"
          disabled={generating}
          slotProps={{ htmlInput: { "data-testid": "generate-course-title" } }}
        />
        <TextField
          label="Topic"
          required
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          fullWidth
          size="small"
          multiline
          minRows={2}
          disabled={generating}
          placeholder="What the course teaches — the subject the objectives are drafted for."
          slotProps={{ htmlInput: { "data-testid": "generate-course-topic" } }}
        />

        <Typography variant="subtitle2" sx={{ mt: 1 }}>
          Learner profile
        </Typography>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
          <TextField
            select
            label="Experience level"
            value={experienceLevel}
            onChange={(e) => setExperienceLevel(e.target.value)}
            size="small"
            disabled={generating}
            sx={{ flex: 1 }}
          >
            <MenuItem value="">
              <em>Unspecified</em>
            </MenuItem>
            {EXPERIENCE_LEVELS.map((lvl) => (
              <MenuItem key={lvl} value={lvl}>
                {lvl}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            label="Role"
            value={learnerRole}
            onChange={(e) => setLearnerRole(e.target.value)}
            size="small"
            disabled={generating}
            sx={{ flex: 1 }}
            placeholder="e.g. junior developer"
          />
        </Stack>

        <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
          <TextField
            label="Target weeks"
            type="number"
            value={targetWeeks}
            onChange={(e) => setTargetWeeks(e.target.value)}
            size="small"
            disabled={generating}
            sx={{ flex: 1 }}
            error={!!targetWeeks && !weeksValid}
            helperText="1–52"
            slotProps={{ htmlInput: { min: 1, max: 52 } }}
          />
          <TextField
            label="Objectives"
            type="number"
            value={objectivesCount}
            onChange={(e) => setObjectivesCount(e.target.value)}
            size="small"
            disabled={generating}
            sx={{ flex: 1 }}
            error={!!objectivesCount && !countValid}
            helperText={`1–${OBJECTIVES_MAX} (cost bound)`}
            slotProps={{
              htmlInput: {
                min: 1,
                max: OBJECTIVES_MAX,
                "data-testid": "generate-course-objectives",
              },
            }}
          />
          <TextField
            label="Hours / week"
            type="number"
            value={hoursPerWeek}
            onChange={(e) => setHoursPerWeek(e.target.value)}
            size="small"
            disabled={generating}
            sx={{ flex: 1 }}
            slotProps={{ htmlInput: { min: 0 } }}
          />
        </Stack>

        {startMutation.isError && (
          <Alert severity="error">
            Failed to start the course generation. AI generation requires an
            author role and a configured API key — check and try again.
          </Alert>
        )}

        {generating && (
          <Box data-testid="generate-course-progress">
            <Typography variant="body2" color="text.secondary" sx={{ mb: 0.75 }}>
              {job
                ? `Generating… ${job.completed_steps}/${job.total_steps}${
                    job.phase ? ` — ${job.phase}` : ""
                  }`
                : "Starting generation…"}
            </Typography>
            <LinearProgress
              variant={job ? "determinate" : "indeterminate"}
              value={pct}
            />
          </Box>
        )}

        {failed && (
          <Alert
            severity="error"
            action={
              <Button color="inherit" size="small" onClick={handleRetry}>
                Retry
              </Button>
            }
          >
            Generation failed{job?.error ? `: ${job.error}` : "."} You can adjust
            the brief and try again.
          </Alert>
        )}

        <Box>
          <Button
            variant="contained"
            onClick={handleSubmit}
            disabled={!canSubmit || generating}
            data-testid="generate-course-btn"
          >
            {generating ? "Generating…" : "Generate course"}
          </Button>
        </Box>
      </Stack>
    </Paper>
  );
}
