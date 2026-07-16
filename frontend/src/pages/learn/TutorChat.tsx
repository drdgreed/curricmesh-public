/**
 * TutorChat — the B6 Q&A chat surface inside the Course Player.
 *
 * A learner types a question; we POST it to the RAG tutor (grounded in the
 * enrolled version's content) and render the answer with its citations. The
 * tutor refuses when it has no supporting context — that refusal string comes
 * back in ``answer`` and is rendered as an ordinary tutor message.
 *
 * Conversation continuity: the first answer returns a ``conversation_id`` which
 * we retain and send back on every subsequent turn so the server threads the
 * exchange. PII redaction is server-side, so we send the raw question.
 *
 * Citations carry a ``source_member_id``; when it maps to an item in this
 * course we render a clickable chip that jumps the player to that item.
 */

import { useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import SendIcon from "@mui/icons-material/Send";

import {
  askTutor,
  type TutorCitation,
  type TutorAnswer,
} from "../../api/tutor";
import type { CourseItem } from "../../api/learn";
import { useSessionLanguage } from "../../i18n/SessionLanguageContext";

interface ChatTurn {
  role: "learner" | "tutor";
  text: string;
  citations?: TutorCitation[];
}

/** Human label for a citation's source item, e.g. "Foundations · Week 1". */
function citationLabel(
  citation: TutorCitation,
  itemsById: Map<string, CourseItem>
): string {
  if (citation.source_member_id) {
    const item = itemsById.get(citation.source_member_id);
    if (item) return `${item.section} · Week ${item.week_index}`;
  }
  // Fall back to a trimmed snippet when the source item isn't in this course.
  const s = citation.snippet.trim();
  return s.length > 48 ? `${s.slice(0, 48)}…` : s || "Source";
}

function CitationList({
  citations,
  itemsById,
  onSelectItem,
}: {
  citations: TutorCitation[];
  itemsById: Map<string, CourseItem>;
  onSelectItem?: (memberId: string) => void;
}) {
  if (citations.length === 0) return null;
  return (
    <Box sx={{ mt: 1 }} data-testid="tutor-citations">
      <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 0.5 }}>
        Sources
      </Typography>
      <Stack direction="row" spacing={0.5} sx={{ flexWrap: "wrap", gap: 0.5 }}>
        {citations.map((c) => {
          const item = c.source_member_id ? itemsById.get(c.source_member_id) : undefined;
          const clickable = !!item && !!onSelectItem;
          return (
            <Chip
              key={c.chunk_id}
              size="small"
              variant="outlined"
              label={citationLabel(c, itemsById)}
              title={c.snippet}
              data-testid="tutor-citation"
              onClick={
                clickable
                  ? () => onSelectItem!(c.source_member_id as string)
                  : undefined
              }
              clickable={clickable}
            />
          );
        })}
      </Stack>
    </Box>
  );
}

export function TutorChat({
  enrollmentId,
  items,
  onSelectItem,
}: {
  enrollmentId: string;
  items: CourseItem[];
  onSelectItem?: (memberId: string) => void;
}) {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [draft, setDraft] = useState("");
  const conversationId = useRef<string | undefined>(undefined);
  // T3b — the learner's session-chosen tutor language rides each request.
  const { language } = useSessionLanguage();

  const itemsById = new Map(items.map((i) => [i.member_id, i]));

  const ask = useMutation({
    mutationFn: (question: string): Promise<TutorAnswer> =>
      askTutor(enrollmentId, {
        question,
        conversationId: conversationId.current,
        language,
      }),
    onSuccess: (data) => {
      conversationId.current = data.conversation_id;
      setTurns((prev) => [
        ...prev,
        { role: "tutor", text: data.answer, citations: data.citations },
      ]);
    },
  });

  function send() {
    const question = draft.trim();
    if (!question || ask.isPending) return;
    setTurns((prev) => [...prev, { role: "learner", text: question }]);
    setDraft("");
    ask.mutate(question);
  }

  return (
    <Paper variant="outlined" sx={{ p: 2 }} data-testid="tutor-chat">
      <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 1 }}>
        Ask the tutor
      </Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1.5 }}>
        Answers are grounded in this course's content.
      </Typography>

      <Stack spacing={1.5} sx={{ mb: 2 }} data-testid="tutor-messages">
        {turns.length === 0 && !ask.isPending && (
          <Typography variant="body2" color="text.secondary">
            Ask a question about this course to get started.
          </Typography>
        )}
        {turns.map((t, i) => (
          <Box
            key={i}
            data-testid={t.role === "learner" ? "tutor-msg-learner" : "tutor-msg-tutor"}
            sx={{
              alignSelf: t.role === "learner" ? "flex-end" : "flex-start",
              maxWidth: "90%",
              bgcolor: t.role === "learner" ? "primary.main" : "action.hover",
              color: t.role === "learner" ? "primary.contrastText" : "text.primary",
              px: 1.5,
              py: 1,
              borderRadius: 2,
            }}
          >
            <Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>
              {t.text}
            </Typography>
            {t.role === "tutor" && t.citations && (
              <CitationList
                citations={t.citations}
                itemsById={itemsById}
                onSelectItem={onSelectItem}
              />
            )}
          </Box>
        ))}
        {ask.isPending && (
          <Box
            data-testid="tutor-loading"
            sx={{ alignSelf: "flex-start", display: "flex", alignItems: "center", gap: 1 }}
          >
            <CircularProgress size={16} />
            <Typography variant="body2" color="text.secondary">
              Thinking…
            </Typography>
          </Box>
        )}
      </Stack>

      {ask.isError && (
        <Alert severity="error" sx={{ mb: 1.5 }}>
          Could not reach the tutor. Please try again.
        </Alert>
      )}

      <Stack direction="row" spacing={1} sx={{ alignItems: "flex-start" }}>
        <TextField
          fullWidth
          size="small"
          multiline
          maxRows={4}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          placeholder="Ask a question…"
          slotProps={{ htmlInput: { "data-testid": "tutor-input" } }}
        />
        <Button
          variant="contained"
          endIcon={<SendIcon />}
          disabled={ask.isPending || !draft.trim()}
          onClick={send}
          data-testid="tutor-send"
        >
          Send
        </Button>
      </Stack>
    </Paper>
  );
}

export default TutorChat;
