/**
 * CopilotPanel — the AI co-pilot right-rail card.
 *
 * Advisory only: the author always decides. Two actions —
 *   • "Get AI guidance" → generates advisor notes (suggestion/question/warning)
 *   • "Suggest prerequisites" → infers dependency edges across the draft
 * Notes render grouped by kind with Accept/Dismiss; accepted/dismissed render
 * muted. Every AI call is 503-graceful: when the server has no ANTHROPIC_API_KEY
 * we show a quiet notice instead of a scary error, and the builder stays usable.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Divider from "@mui/material/Divider";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import type {
  AdvisorNote,
  AdvisorNoteStatus,
  CourseOut,
  InferDepsResult,
} from "../../api/builder";
import {
  advise,
  inferDeps,
  isAiNotConfigured,
  listAdvisorNotes,
  updateAdvisorNote,
} from "../../api/builder";

type NoteKind = AdvisorNote["kind"];

const KIND_COLOR: Record<NoteKind, "info" | "primary" | "warning"> = {
  question: "info",
  suggestion: "primary",
  warning: "warning",
};

function NoteCard({
  note,
  onUpdate,
  pending,
}: {
  note: AdvisorNote;
  onUpdate: (status: AdvisorNoteStatus) => void;
  pending: boolean;
}) {
  const open = note.status === "open";
  return (
    <Box
      data-testid="advisor-note"
      sx={{
        p: 1.25,
        borderRadius: 1.5,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
        opacity: open ? 1 : 0.6,
      }}
    >
      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5, mb: 0.5 }}>
        <Chip
          label={note.kind}
          size="small"
          color={KIND_COLOR[note.kind] ?? "default"}
          variant="outlined"
          sx={{ height: 20, fontSize: 11 }}
        />
        {!open && (
          <Chip
            label={note.status}
            size="small"
            sx={{ height: 20, fontSize: 11 }}
          />
        )}
      </Box>
      <Typography variant="body2">{note.text}</Typography>
      {open && (
        <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
          <Button
            size="small"
            variant="outlined"
            disabled={pending}
            onClick={() => onUpdate("accepted")}
            data-testid="advisor-note-accept"
          >
            Accept
          </Button>
          <Button
            size="small"
            color="inherit"
            disabled={pending}
            onClick={() => onUpdate("dismissed")}
            data-testid="advisor-note-dismiss"
          >
            Dismiss
          </Button>
        </Stack>
      )}
    </Box>
  );
}

export function CopilotPanel({ course }: { course: CourseOut }) {
  const queryClient = useQueryClient();
  const courseId = course.id;

  const notesQuery = useQuery({
    queryKey: ["builder-advisor-notes", courseId],
    queryFn: () => listAdvisorNotes(courseId),
  });

  function invalidateNotes() {
    queryClient.invalidateQueries({
      queryKey: ["builder-advisor-notes", courseId],
    });
  }

  const inferMutation = useMutation<InferDepsResult>({
    mutationFn: () => inferDeps(courseId),
    onSuccess: () => {
      invalidateNotes();
      queryClient.invalidateQueries({
        queryKey: ["builder-dependencies", courseId],
      });
    },
  });

  const adviseMutation = useMutation({
    mutationFn: () => advise(courseId),
    onSuccess: () => {
      invalidateNotes();
      inferMutation.reset();
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: AdvisorNoteStatus }) =>
      updateAdvisorNote(id, status),
    onSuccess: invalidateNotes,
  });

  // Surface the friendly "not configured" notice if any AI action 503s.
  const aiNotConfigured =
    isAiNotConfigured(adviseMutation.error) ||
    isAiNotConfigured(inferMutation.error) ||
    isAiNotConfigured(updateMutation.error);

  const notes = notesQuery.data ?? [];
  // Open notes first, then accepted/dismissed (muted).
  const ordered = [...notes].sort((a, b) => {
    const ao = a.status === "open" ? 0 : 1;
    const bo = b.status === "open" ? 0 : 1;
    return ao - bo;
  });

  const inferResult = inferMutation.data;

  return (
    <Paper variant="outlined" sx={{ p: 2.5 }} data-testid="copilot-panel">
      <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
        AI Co-pilot
      </Typography>
      <Typography variant="caption" color="text.secondary">
        Advisory — you decide.
      </Typography>

      <Stack spacing={1} sx={{ mt: 1.5 }}>
        <Button
          variant="outlined"
          fullWidth
          disabled={adviseMutation.isPending}
          onClick={() => adviseMutation.mutate()}
          data-testid="copilot-advise-btn"
        >
          {adviseMutation.isPending ? "Thinking…" : "Get AI guidance"}
        </Button>
        <Button
          variant="outlined"
          fullWidth
          disabled={inferMutation.isPending}
          onClick={() => inferMutation.mutate()}
          data-testid="copilot-infer-btn"
        >
          {inferMutation.isPending ? "Thinking…" : "Suggest prerequisites"}
        </Button>
      </Stack>

      {inferResult && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 1 }}
          data-testid="copilot-infer-result"
        >
          {inferResult.suggested_created} prerequisite
          {inferResult.suggested_created === 1 ? "" : "s"} suggested ·{" "}
          {inferResult.missing_flagged} gap
          {inferResult.missing_flagged === 1 ? "" : "s"} flagged
        </Typography>
      )}

      {aiNotConfigured && (
        <Alert
          severity="info"
          sx={{ mt: 1.5 }}
          data-testid="copilot-ai-not-configured"
        >
          AI guidance isn't configured on the server.
        </Alert>
      )}

      <Divider sx={{ my: 2 }} />

      {ordered.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          No guidance yet. Ask the co-pilot for suggestions.
        </Typography>
      ) : (
        <Stack spacing={1}>
          {ordered.map((note) => (
            <NoteCard
              key={note.id}
              note={note}
              pending={updateMutation.isPending}
              onUpdate={(status) =>
                updateMutation.mutate({ id: note.id, status })
              }
            />
          ))}
        </Stack>
      )}
    </Paper>
  );
}
