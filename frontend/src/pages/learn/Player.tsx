/**
 * Player — opens an enrollment and delivers the pinned course:
 *   left rail  : items grouped by week (with per-item progress ticks)
 *   center     : the selected item's rendered content (markdown + inline media
 *                embeds via LessonContent), a "Mark complete" action, and — for
 *                ``assessment`` items — a response textarea → Submit.
 *
 * Completing an item re-fetches the structure so the ticks + header progress
 * update immediately, and invalidates the enrollments list so My Courses stays
 * in sync.
 */

import { useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Divider from "@mui/material/Divider";
import LinearProgress from "@mui/material/LinearProgress";
import List from "@mui/material/List";
import ListItemButton from "@mui/material/ListItemButton";
import ListItemIcon from "@mui/material/ListItemIcon";
import ListItemText from "@mui/material/ListItemText";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import RadioButtonUncheckedIcon from "@mui/icons-material/RadioButtonUnchecked";
import AutorenewIcon from "@mui/icons-material/Autorenew";

import SlideshowIcon from "@mui/icons-material/Slideshow";
import PictureAsPdfIcon from "@mui/icons-material/PictureAsPdf";
import SlideshowOutlinedIcon from "@mui/icons-material/SlideshowOutlined";

import { PageHeader } from "../../components/PageHeader";
import { LanguageSelector } from "../../i18n/LanguageSelector";
import { LessonContent } from "./LessonContent";
import { TutorChat } from "./TutorChat";
import {
  getCourse,
  getCourseDecks,
  setProgress,
  submitAssessment,
  type CourseItem,
  type DeckOut,
} from "../../api/learn";
import { ASSET_KIND_LABELS, type AssetKind } from "../../api/client";

function kindLabel(kind: string): string {
  return ASSET_KIND_LABELS[kind as AssetKind] ?? kind;
}

function ProgressTick({ status }: { status: string }) {
  if (status === "complete") {
    return <CheckCircleIcon color="success" fontSize="small" />;
  }
  if (status === "in_progress") {
    return <AutorenewIcon color="warning" fontSize="small" />;
  }
  return <RadioButtonUncheckedIcon color="disabled" fontSize="small" />;
}

/** Left rail: items grouped by week, each with a progress tick. */
function ItemRail({
  items,
  selectedId,
  onSelect,
}: {
  items: CourseItem[];
  selectedId: string | null;
  onSelect: (memberId: string) => void;
}) {
  const weeks = useMemo(() => {
    const byWeek = new Map<number, CourseItem[]>();
    for (const it of items) {
      const arr = byWeek.get(it.week_index) ?? [];
      arr.push(it);
      byWeek.set(it.week_index, arr);
    }
    return [...byWeek.entries()].sort((a, b) => a[0] - b[0]);
  }, [items]);

  return (
    <Paper variant="outlined" sx={{ p: 1 }} data-testid="item-rail">
      {weeks.map(([week, weekItems]) => (
        <Box key={week} sx={{ mb: 1 }}>
          <Typography
            variant="overline"
            color="text.secondary"
            sx={{ px: 1.5, display: "block" }}
          >
            Week {week}
          </Typography>
          <List dense disablePadding>
            {weekItems.map((it) => (
              <ListItemButton
                key={it.member_id}
                selected={it.member_id === selectedId}
                onClick={() => onSelect(it.member_id)}
                sx={{ borderRadius: 1 }}
                data-testid="rail-item"
              >
                <ListItemIcon sx={{ minWidth: 34 }}>
                  <ProgressTick status={it.progress_status} />
                </ListItemIcon>
                <ListItemText
                  primary={kindLabel(it.kind)}
                  secondary={it.section}
                  slotProps={{
                    primary: { sx: { fontSize: "0.9rem" } },
                    secondary: { sx: { fontSize: "0.75rem" } },
                  }}
                />
              </ListItemButton>
            ))}
          </List>
        </Box>
      ))}
    </Paper>
  );
}

/** Assessment response form → Submit. */
function AssessmentPanel({
  onSubmit,
  pending,
  submitted,
}: {
  onSubmit: (text: string) => void;
  pending: boolean;
  submitted: boolean;
}) {
  const [text, setText] = useState("");
  return (
    <Box sx={{ mt: 2 }} data-testid="assessment-panel">
      <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 1 }}>
        Your response
      </Typography>
      <TextField
        multiline
        minRows={4}
        fullWidth
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Write your answer…"
        slotProps={{ htmlInput: { "data-testid": "assessment-input" } }}
      />
      <Stack direction="row" spacing={1} sx={{ mt: 1.5, alignItems: "center" }}>
        <Button
          variant="contained"
          disabled={pending || !text.trim()}
          onClick={() => onSubmit(text.trim())}
          data-testid="submit-btn"
        >
          {pending ? "Submitting…" : "Submit response"}
        </Button>
        {submitted && (
          <Chip size="small" color="success" label="Response submitted" />
        )}
      </Stack>
    </Box>
  );
}

/** A single rendered deck: embedded HTML slides + PDF/PPTX download buttons. */
function DeckCard({ deck, index }: { deck: DeckOut; index: number }) {
  return (
    <Box sx={{ mb: index > 0 ? 3 : 0 }} data-testid="deck-card">
      <Box
        component="iframe"
        title={`Slide deck ${index + 1}`}
        src={deck.html_url}
        data-testid="deck-frame"
        sx={{
          width: "100%",
          height: 420,
          border: 0,
          borderRadius: 1,
          bgcolor: "background.default",
        }}
      />
      <Stack direction="row" spacing={1} sx={{ mt: 1.5 }}>
        <Button
          size="small"
          variant="outlined"
          startIcon={<PictureAsPdfIcon />}
          component="a"
          href={deck.pdf_url}
          target="_blank"
          rel="noopener"
          data-testid="deck-download-pdf"
        >
          Download PDF
        </Button>
        <Button
          size="small"
          variant="outlined"
          startIcon={<SlideshowOutlinedIcon />}
          component="a"
          href={deck.pptx_url}
          target="_blank"
          rel="noopener"
          data-testid="deck-download-pptx"
        >
          Download PPTX
        </Button>
      </Stack>
    </Box>
  );
}

/**
 * "Slides / Deck" surface — decks belong to the pinned course version, so this
 * is mounted once in the player's center column (below the item detail, above
 * the tutor). Renders nothing when the course has no decks (graceful absence).
 */
function DeckPanel({ enrollmentId }: { enrollmentId: string }) {
  const decksQuery = useQuery({
    queryKey: ["learn", "decks", enrollmentId],
    queryFn: () => getCourseDecks(enrollmentId),
    enabled: !!enrollmentId,
  });

  const decks = decksQuery.data;
  // Graceful absence: no decks (or still loading / errored) → render nothing so
  // the surface never clutters a course that has none.
  if (!decks || decks.length === 0) {
    return null;
  }

  return (
    <Paper variant="outlined" sx={{ p: 3 }} data-testid="deck-panel">
      <Stack direction="row" spacing={1} sx={{ alignItems: "center", mb: 2 }}>
        <SlideshowIcon color="primary" />
        <Typography variant="h6">Slides</Typography>
      </Stack>
      <Divider sx={{ mb: 2 }} />
      {decks.map((deck, i) => (
        <DeckCard key={deck.id} deck={deck} index={i} />
      ))}
    </Paper>
  );
}

export function Player() {
  const { enrollmentId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const courseQuery = useQuery({
    queryKey: ["learn", "course", enrollmentId],
    queryFn: () => getCourse(enrollmentId),
    enabled: !!enrollmentId,
  });

  const progressMutation = useMutation({
    mutationFn: (memberId: string) =>
      setProgress(enrollmentId, memberId, "complete"),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["learn", "course", enrollmentId],
      });
      queryClient.invalidateQueries({ queryKey: ["learn", "enrollments"] });
    },
  });

  const submitMutation = useMutation({
    mutationFn: ({ memberId, text }: { memberId: string; text: string }) =>
      submitAssessment(enrollmentId, memberId, text),
  });

  if (courseQuery.isLoading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (courseQuery.isError || !courseQuery.data) {
    return (
      <Alert severity="error">
        Could not load this course.{" "}
        <Button size="small" onClick={() => navigate("/learn")}>
          Back to My Courses
        </Button>
      </Alert>
    );
  }

  const course = courseQuery.data;
  const items = course.items;
  const selected =
    items.find((i) => i.member_id === selectedId) ?? items[0] ?? null;
  const percent =
    course.total_items > 0
      ? Math.round((course.completed_items / course.total_items) * 100)
      : 0;

  return (
    <Box>
      <PageHeader
        title={course.title}
        subtitle={`${course.completed_items} / ${course.total_items} items complete · ${percent}%`}
        actions={
          <Stack direction="row" spacing={1.5} sx={{ alignItems: "center" }}>
            <LanguageSelector />
            <Button variant="outlined" onClick={() => navigate("/learn")}>
              My Courses
            </Button>
          </Stack>
        }
      />
      <LinearProgress
        variant="determinate"
        value={percent}
        sx={{ height: 8, borderRadius: 1, mb: 3 }}
        data-testid="course-progress"
      />

      <Stack
        direction={{ xs: "column", md: "row" }}
        spacing={3}
        sx={{ alignItems: "flex-start" }}
      >
        <Box sx={{ width: { xs: "100%", md: 300 }, flexShrink: 0 }}>
          <ItemRail
            items={items}
            selectedId={selected?.member_id ?? null}
            onSelect={setSelectedId}
          />
        </Box>

        <Box sx={{ flexGrow: 1, minWidth: 0, width: "100%" }}>
          {selected ? (
            <Paper variant="outlined" sx={{ p: 3 }} data-testid="item-detail">
              <Stack
                direction="row"
                sx={{ justifyContent: "space-between", alignItems: "center", mb: 1 }}
              >
                <Box>
                  <Typography variant="h6">{kindLabel(selected.kind)}</Typography>
                  <Typography variant="body2" color="text.secondary">
                    {selected.section} · Week {selected.week_index}
                  </Typography>
                </Box>
                <ProgressTick status={selected.progress_status} />
              </Stack>
              <Divider sx={{ mb: 2 }} />

              <LessonContent
                kind={selected.kind}
                content={selected.content}
                media={selected.media}
              />

              <Box sx={{ mt: 3 }}>
                <Button
                  variant="contained"
                  color="success"
                  disabled={
                    selected.progress_status === "complete" ||
                    progressMutation.isPending
                  }
                  onClick={() => progressMutation.mutate(selected.member_id)}
                  data-testid="mark-complete-btn"
                >
                  {selected.progress_status === "complete"
                    ? "Completed"
                    : progressMutation.isPending
                      ? "Saving…"
                      : "Mark complete"}
                </Button>
              </Box>

              {selected.kind === "assessment" && (
                <AssessmentPanel
                  key={selected.member_id}
                  pending={submitMutation.isPending}
                  submitted={
                    submitMutation.data?.content_member_id === selected.member_id
                  }
                  onSubmit={(text) =>
                    submitMutation.mutate({ memberId: selected.member_id, text })
                  }
                />
              )}
            </Paper>
          ) : (
            <Alert severity="info">This course has no items yet.</Alert>
          )}

          <Box sx={{ mt: 3 }}>
            <DeckPanel enrollmentId={enrollmentId} />
          </Box>

          <Box sx={{ mt: 3 }}>
            <TutorChat
              enrollmentId={enrollmentId}
              items={items}
              onSelectItem={setSelectedId}
            />
          </Box>
        </Box>
      </Stack>
    </Box>
  );
}

export default Player;
