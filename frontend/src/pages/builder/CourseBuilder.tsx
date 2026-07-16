/**
 * CourseBuilder — the Course Builder shell (Task 8).
 *
 * No course selected → show the "New course" setup form (plus a picker for any
 * existing drafts). Once a draft is active → show the objective/content canvas
 * with a publish panel. One cohesive page; sub-components live alongside it.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import FormControl from "@mui/material/FormControl";
import InputLabel from "@mui/material/InputLabel";
import MenuItem from "@mui/material/MenuItem";
import Select from "@mui/material/Select";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import type { CourseOut } from "../../api/builder";
import { getCourse, listCourses } from "../../api/builder";
import { SetupStep } from "./SetupStep";
import { GenerateCourseStep } from "./GenerateCourseStep";
import { ObjectiveCanvas } from "./ObjectiveCanvas";
import { PublishPanel } from "./PublishPanel";
import { CopilotPanel } from "./CopilotPanel";
import { MediaLibraryPanel } from "./MediaLibraryPanel";

export function CourseBuilder() {
  const [activeId, setActiveId] = useState<string>("");

  const coursesQuery = useQuery({
    queryKey: ["builder-courses"],
    queryFn: listCourses,
  });

  // Keep the active course fresh (status/curriculum_id flip on publish).
  const courseQuery = useQuery({
    queryKey: ["builder-course", activeId],
    queryFn: () => getCourse(activeId),
    enabled: !!activeId,
  });

  const courses = coursesQuery.data ?? [];
  const active: CourseOut | undefined = courseQuery.data;

  function handleCreated(course: CourseOut) {
    setActiveId(course.id);
  }

  if (coursesQuery.isLoading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", mt: 8 }}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box>
      <Stack
        direction={{ xs: "column", sm: "row" }}
        spacing={2}
        sx={{ alignItems: { sm: "center" }, justifyContent: "space-between", mb: 1 }}
      >
        <Box>
          <Typography variant="h5" gutterBottom>
            Course Builder
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Draft a course — objectives, content, weekly load — then publish it
            into CurricMesh as a versioned curriculum.
          </Typography>
        </Box>
        {activeId && (
          <Button variant="outlined" onClick={() => setActiveId("")}>
            New / switch course
          </Button>
        )}
      </Stack>

      {coursesQuery.isError && (
        <Alert severity="error" sx={{ mb: 2 }}>
          Failed to load draft courses. The Course Builder requires an author
          role (architect, program manager, or instructor).
        </Alert>
      )}

      {!activeId && (
        <Stack spacing={3} sx={{ mt: 2 }}>
          {courses.length > 0 && (
            <FormControl size="small" sx={{ minWidth: 320, maxWidth: 480 }}>
              <InputLabel id="builder-course-label">
                Open an existing draft
              </InputLabel>
              <Select
                labelId="builder-course-label"
                label="Open an existing draft"
                value=""
                onChange={(e) => setActiveId(e.target.value)}
                data-testid="open-draft-select"
              >
                {courses.map((c) => (
                  <MenuItem key={c.id} value={c.id}>
                    {c.title}{" "}
                    {c.status === "published" ? " (published)" : ""}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          )}
          <GenerateCourseStep onCreated={(courseId) => setActiveId(courseId)} />
          <Typography variant="body2" color="text.secondary">
            …or start from a blank draft:
          </Typography>
          <SetupStep onCreated={handleCreated} />
        </Stack>
      )}

      {activeId && courseQuery.isLoading && (
        <Box sx={{ display: "flex", justifyContent: "center", mt: 6 }}>
          <CircularProgress />
        </Box>
      )}

      {active && (
        <Box sx={{ mt: 2 }}>
          <Stack
            direction="row"
            spacing={1.5}
            sx={{ alignItems: "center", mb: 2 }}
          >
            <Typography variant="h6">{active.title}</Typography>
            <Chip
              label={active.status}
              size="small"
              color={active.status === "published" ? "success" : "default"}
            />
          </Stack>

          <Box
            sx={{
              display: "grid",
              // xl (≥1536px) for the 2-column split: at lg (1280px) the nested
              // ObjectiveCanvas 3-col grid leaves only ~36px for its centre
              // column, forcing a 1524px page-width and a horizontal scrollbar.
              // With the split deferred to xl the canvas has ≥984px at 1280px.
              gridTemplateColumns: { xs: "1fr", xl: "1fr 340px" },
              gap: 2,
              alignItems: "start",
            }}
          >
            <ObjectiveCanvas course={active} />
            <Stack spacing={2}>
              <PublishPanel course={active} />
              <MediaLibraryPanel />
              <CopilotPanel course={active} />
            </Stack>
          </Box>
        </Box>
      )}
    </Box>
  );
}
