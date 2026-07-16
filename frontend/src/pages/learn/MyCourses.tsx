/**
 * MyCourses — the learner's hub (``GET /learn/enrollments``): each enrolled
 * course with a completed/total progress bar and a link into the Player.
 */

import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardActionArea from "@mui/material/CardActionArea";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import LinearProgress from "@mui/material/LinearProgress";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import { PageHeader } from "../../components/PageHeader";
import { getEnrollments, type EnrollmentOut } from "../../api/learn";

function pct(done: number, total: number): number {
  return total > 0 ? Math.round((done / total) * 100) : 0;
}

function CourseCard({
  enrollment,
  onOpen,
}: {
  enrollment: EnrollmentOut;
  onOpen: () => void;
}) {
  const percent = pct(enrollment.completed_items, enrollment.total_items);
  const done = enrollment.status === "completed";
  return (
    <Card variant="outlined" data-testid="enrollment-card">
      <CardActionArea onClick={onOpen}>
        <CardContent>
          <Stack
            direction="row"
            sx={{ justifyContent: "space-between", alignItems: "center", gap: 2 }}
          >
            <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
              {enrollment.title}
            </Typography>
            {done && <Chip size="small" color="success" label="Completed" />}
          </Stack>
          <Box sx={{ mt: 1.5 }}>
            <LinearProgress
              variant="determinate"
              value={percent}
              sx={{ height: 8, borderRadius: 1 }}
              data-testid="progress-bar"
            />
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ mt: 0.5, display: "block" }}
            >
              {enrollment.completed_items} / {enrollment.total_items} items ·{" "}
              {percent}%
            </Typography>
          </Box>
        </CardContent>
      </CardActionArea>
    </Card>
  );
}

export function MyCourses() {
  const navigate = useNavigate();
  const query = useQuery({
    queryKey: ["learn", "enrollments"],
    queryFn: getEnrollments,
  });

  if (query.isLoading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (query.isError) {
    return (
      <Alert severity="error">Could not load your courses. Please try again.</Alert>
    );
  }

  const enrollments = query.data ?? [];

  return (
    <Box>
      <PageHeader
        title="My Courses"
        subtitle="Your enrolled courses and progress."
        actions={
          <Button variant="contained" onClick={() => navigate("/learn/catalog")}>
            Browse catalog
          </Button>
        }
      />

      {enrollments.length === 0 ? (
        <Alert severity="info">
          You're not enrolled in any courses yet. Browse the catalog to get started.
        </Alert>
      ) : (
        <Stack spacing={2}>
          {enrollments.map((e) => (
            <CourseCard
              key={e.id}
              enrollment={e}
              onOpen={() => navigate(`/learn/courses/${e.id}`)}
            />
          ))}
        </Stack>
      )}
    </Box>
  );
}

export default MyCourses;
