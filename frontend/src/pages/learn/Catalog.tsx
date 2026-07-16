/**
 * Catalog — the learner browses released courses (``GET /learn/catalog``) and
 * self-enrolls (``POST /learn/enroll``). An already-enrolled course returns 409,
 * which we surface as a friendly "already enrolled" state rather than an error.
 */

import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import { PageHeader } from "../../components/PageHeader";
import {
  enroll,
  getCatalog,
  getEnrollments,
  type CatalogEntry,
} from "../../api/learn";

function CatalogCard({
  entry,
  enrolled,
  onEnroll,
  pending,
}: {
  entry: CatalogEntry;
  enrolled: boolean;
  onEnroll: (id: string) => void;
  pending: boolean;
}) {
  return (
    <Card variant="outlined" data-testid="catalog-card">
      <CardContent>
        <Stack
          direction="row"
          sx={{ justifyContent: "space-between", alignItems: "flex-start", gap: 2 }}
        >
          <Box>
            <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
              {entry.title}
            </Typography>
            <Chip
              size="small"
              label={`v${entry.version}`}
              variant="outlined"
              sx={{ mt: 0.75 }}
            />
          </Box>
          {enrolled ? (
            <Chip size="small" color="success" label="Enrolled" />
          ) : (
            <Button
              variant="contained"
              size="small"
              disabled={pending}
              onClick={() => onEnroll(entry.curriculum_version_id)}
              data-testid="enroll-btn"
            >
              {pending ? "Enrolling…" : "Enroll"}
            </Button>
          )}
        </Stack>
      </CardContent>
    </Card>
  );
}

export function Catalog() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const catalogQuery = useQuery({
    queryKey: ["learn", "catalog"],
    queryFn: getCatalog,
  });
  const enrollmentsQuery = useQuery({
    queryKey: ["learn", "enrollments"],
    queryFn: getEnrollments,
  });

  const enrolledVersionIds = useMemo(
    () =>
      new Set(
        (enrollmentsQuery.data ?? []).map((e) => e.curriculum_version_id)
      ),
    [enrollmentsQuery.data]
  );

  const enrollMutation = useMutation({
    mutationFn: (curriculumVersionId: string) => enroll(curriculumVersionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["learn", "enrollments"] });
      navigate("/learn");
    },
    onError: (err) => {
      // 409 = already enrolled: refresh enrollments so the card flips to
      // "Enrolled" rather than surfacing a hard error.
      if (isAxiosError(err) && err.response?.status === 409) {
        queryClient.invalidateQueries({ queryKey: ["learn", "enrollments"] });
      }
    },
  });

  if (catalogQuery.isLoading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (catalogQuery.isError) {
    return (
      <Alert severity="error">
        Could not load the course catalog. Please try again.
      </Alert>
    );
  }

  const entries = catalogQuery.data ?? [];
  const conflict =
    enrollMutation.isError &&
    isAxiosError(enrollMutation.error) &&
    enrollMutation.error.response?.status === 409;

  return (
    <Box>
      <PageHeader
        title="Course Catalog"
        subtitle="Browse released courses and enroll to start learning."
        actions={
          <Button variant="outlined" onClick={() => navigate("/learn")}>
            My Courses
          </Button>
        }
      />

      {conflict && (
        <Alert severity="info" sx={{ mb: 2 }}>
          You're already enrolled in that course.
        </Alert>
      )}

      {entries.length === 0 ? (
        <Alert severity="info">No released courses are available yet.</Alert>
      ) : (
        <Stack spacing={2}>
          {entries.map((entry) => (
            <CatalogCard
              key={entry.curriculum_version_id}
              entry={entry}
              enrolled={enrolledVersionIds.has(entry.curriculum_version_id)}
              pending={
                enrollMutation.isPending &&
                enrollMutation.variables === entry.curriculum_version_id
              }
              onEnroll={(id) => enrollMutation.mutate(id)}
            />
          ))}
        </Stack>
      )}
    </Box>
  );
}

export default Catalog;
