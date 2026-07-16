/**
 * SetupStep — the "New course" form. Collects a title (required) plus a
 * lightweight learner profile and a target-week count, then POSTs to
 * createCourse and hands the new course back to the shell.
 */

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import type { CourseOut, LearnerProfile } from "../../api/builder";
import { createCourse } from "../../api/builder";

const EXPERIENCE_LEVELS = ["beginner", "intermediate", "advanced"];

export function SetupStep({
  onCreated,
}: {
  onCreated: (course: CourseOut) => void;
}) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [experienceLevel, setExperienceLevel] = useState("");
  const [learnerRole, setLearnerRole] = useState("");
  const [goals, setGoals] = useState("");
  const [weeklyHours, setWeeklyHours] = useState("");
  const [motivation, setMotivation] = useState("");
  const [targetWeeks, setTargetWeeks] = useState("");

  const mutation = useMutation({
    mutationFn: createCourse,
    onSuccess: onCreated,
  });

  function handleSubmit() {
    if (!title.trim()) return;

    const profile: LearnerProfile = {};
    if (experienceLevel) profile.experience_level = experienceLevel;
    if (learnerRole.trim()) profile.role = learnerRole.trim();
    if (goals.trim()) profile.goals = goals.trim();
    if (motivation.trim()) profile.motivation = motivation.trim();
    const hours = parseFloat(weeklyHours);
    if (!Number.isNaN(hours)) profile.weekly_hours_target = hours;

    const weeks = parseInt(targetWeeks, 10);

    mutation.mutate({
      title: title.trim(),
      description: description.trim() || undefined,
      learner_profile: Object.keys(profile).length > 0 ? profile : undefined,
      target_weeks: Number.isNaN(weeks) ? undefined : weeks,
    });
  }

  return (
    <Paper
      variant="outlined"
      sx={{ p: 3, maxWidth: 640 }}
      data-testid="new-course-form"
    >
      <Typography variant="h6" gutterBottom>
        New course
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2.5 }}>
        Start a draft. You can add objectives and content next, then publish it
        into CurricMesh as a versioned curriculum.
      </Typography>

      <Stack spacing={2}>
        <TextField
          label="Title"
          required
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          fullWidth
          size="small"
          slotProps={{ htmlInput: { "data-testid": "new-course-title" } }}
        />
        <TextField
          label="Description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          fullWidth
          size="small"
          multiline
          minRows={2}
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
            sx={{ flex: 1 }}
            placeholder="e.g. junior developer"
          />
        </Stack>

        <TextField
          label="Goals"
          value={goals}
          onChange={(e) => setGoals(e.target.value)}
          fullWidth
          size="small"
        />

        <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
          <TextField
            label="Weekly hours target"
            type="number"
            value={weeklyHours}
            onChange={(e) => setWeeklyHours(e.target.value)}
            size="small"
            sx={{ flex: 1 }}
            slotProps={{
              htmlInput: { "data-testid": "new-course-weekly-hours", min: 0 },
            }}
          />
          <TextField
            label="Target weeks"
            type="number"
            value={targetWeeks}
            onChange={(e) => setTargetWeeks(e.target.value)}
            size="small"
            sx={{ flex: 1 }}
            slotProps={{ htmlInput: { min: 0 } }}
          />
        </Stack>

        <TextField
          label="Motivation"
          value={motivation}
          onChange={(e) => setMotivation(e.target.value)}
          fullWidth
          size="small"
        />

        {mutation.isError && (
          <Alert severity="error">
            Failed to create the course. Check your role and try again.
          </Alert>
        )}

        <Box>
          <Button
            variant="contained"
            onClick={handleSubmit}
            disabled={!title.trim() || mutation.isPending}
            data-testid="create-course-btn"
          >
            {mutation.isPending ? "Creating…" : "Create course"}
          </Button>
        </Box>
      </Stack>
    </Paper>
  );
}
