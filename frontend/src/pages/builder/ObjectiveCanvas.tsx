/**
 * ObjectiveCanvas — the authoring surface for a draft course.
 *
 * Three columns:
 *   • left rail  — objectives (add + list, grouped by week)
 *   • center     — quick-capture box: paste a title + content + week, attach to
 *                  an objective. The backend auto-categorizes the kind and
 *                  estimates effort; we show both back on the item card.
 *   • right rail — per-week effort/overload meter (effort + overload queries,
 *                  invalidated on every add).
 */

import { useState, type KeyboardEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Divider from "@mui/material/Divider";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import type {
  BloomLevel,
  CourseOut,
  GeneratedAssessment,
  GeneratedObjective,
  ItemOut,
  ObjectiveOut,
  OverloadWeek,
} from "../../api/builder";
import {
  BLOOM_LEVELS,
  addItem,
  addObjective,
  alignItem,
  attachMedia,
  categorizeItemAI,
  detachMedia,
  generateAssessment,
  generateItemContent,
  generateObjectives,
  getOverload,
  isAiNotConfigured,
  listItemMedia,
  listItems,
  listObjectives,
  updateItem,
} from "../../api/builder";
import { listReadyMedia } from "../../api/media";
import { ASSET_KIND_LABELS, type AssetKind } from "../../api/client";

function kindLabel(kind: string): string {
  return ASSET_KIND_LABELS[kind as AssetKind] ?? kind;
}

function weekLabel(week: number | null | undefined): string {
  return week == null || week === 0 ? "Unscheduled" : `Week ${week}`;
}

// ---------------------------------------------------------------------------
// Shared AI helpers — caveats surface + a scrollable markdown draft preview
// ---------------------------------------------------------------------------

/** Surface an AI draft's `caveats` — the things a human must verify. */
function Caveats({ caveats }: { caveats: string[] }) {
  if (caveats.length === 0) return null;
  return (
    <Box sx={{ mt: 0.75 }} data-testid="ai-caveats">
      <Typography
        variant="caption"
        color="warning.main"
        sx={{ fontWeight: 600, display: "block" }}
      >
        Verify before publishing:
      </Typography>
      <Box component="ul" sx={{ m: 0, pl: 2 }}>
        {caveats.map((c, i) => (
          <Typography
            key={i}
            component="li"
            variant="caption"
            color="text.secondary"
          >
            {c}
          </Typography>
        ))}
      </Box>
    </Box>
  );
}

/** A read-only, scrollable preview of a generated Markdown body. */
function MarkdownPreview({ text }: { text: string }) {
  return (
    <Typography
      variant="caption"
      component="pre"
      sx={{
        whiteSpace: "pre-wrap",
        fontFamily: "monospace",
        m: 0,
        maxHeight: 220,
        overflow: "auto",
        display: "block",
      }}
    >
      {text}
    </Typography>
  );
}

// ---------------------------------------------------------------------------
// Generate objectives with AI (course-level, advisory → accept per objective)
// ---------------------------------------------------------------------------

function GenerateObjectivesPanel({
  courseId,
  onChanged,
}: {
  courseId: string;
  onChanged: () => void;
}) {
  // Which drafts the author has already accepted into the course (by index).
  const [added, setAdded] = useState<Set<number>>(new Set());

  const generateMutation = useMutation({
    mutationFn: () => generateObjectives(courseId),
    onSuccess: () => setAdded(new Set()),
  });
  const addMutation = useMutation({
    mutationFn: (o: GeneratedObjective) =>
      addObjective(courseId, {
        text: o.text,
        bloom_level: o.bloom_level,
        key_skills: o.key_skills,
      }),
    onSuccess: () => onChanged(),
  });

  const drafts = generateMutation.data?.objectives ?? [];
  const aiNotConfigured = isAiNotConfigured(generateMutation.error);

  function accept(index: number, o: GeneratedObjective) {
    addMutation.mutate(o, {
      onSuccess: () => setAdded((prev) => new Set(prev).add(index)),
    });
  }

  return (
    <Box sx={{ mt: 1.5 }} data-testid="generate-objectives">
      <Button
        size="small"
        onClick={() => generateMutation.mutate()}
        disabled={generateMutation.isPending}
        data-testid="generate-objectives-btn"
        startIcon={
          generateMutation.isPending ? (
            <CircularProgress size={12} />
          ) : undefined
        }
      >
        ✨ Generate objectives with AI
      </Button>

      {aiNotConfigured && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 0.5 }}
          data-testid="objectives-ai-not-configured"
        >
          AI not configured
        </Typography>
      )}
      {generateMutation.isError && !aiNotConfigured && (
        <Alert severity="error" sx={{ mt: 0.5 }}>
          Failed to generate objectives.
        </Alert>
      )}

      {drafts.length > 0 && (
        <Stack spacing={1} sx={{ mt: 1 }} data-testid="generated-objectives">
          <Typography variant="caption" color="text.secondary">
            Review and add the drafts you want:
          </Typography>
          {drafts.map((o, i) => (
            <Box
              key={i}
              data-testid="generated-objective"
              sx={{
                p: 1,
                borderRadius: 1.5,
                border: "1px solid",
                borderColor: "divider",
              }}
            >
              <Typography variant="body2" sx={{ fontWeight: 500 }}>
                {o.text}
              </Typography>
              <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5, mt: 0.5 }}>
                <Chip
                  label={o.bloom_level}
                  size="small"
                  variant="outlined"
                  sx={{ height: 20, fontSize: 11 }}
                />
                {o.key_skills.map((s) => (
                  <Chip
                    key={s}
                    label={s}
                    size="small"
                    sx={{ height: 20, fontSize: 11 }}
                  />
                ))}
              </Box>
              <Button
                size="small"
                variant="outlined"
                sx={{ mt: 0.75 }}
                disabled={added.has(i) || addMutation.isPending}
                onClick={() => accept(i, o)}
                data-testid="generated-objective-add"
              >
                {added.has(i) ? "Added ✓" : "Add"}
              </Button>
            </Box>
          ))}
        </Stack>
      )}
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Objective card — the objective + a per-objective "Generate assessment" AI op
// ---------------------------------------------------------------------------

function ObjectiveCard({
  objective,
  courseId,
  onChanged,
}: {
  objective: ObjectiveOut;
  courseId: string;
  onChanged: () => void;
}) {
  // Advisory: generate returns a draft; it is written into the draft course
  // only when the author clicks Apply (creates an aligned assessment item).
  const generateMutation = useMutation({
    mutationFn: () => generateAssessment(objective.id),
  });
  const applyMutation = useMutation({
    mutationFn: async (draft: GeneratedAssessment) => {
      const body = `${draft.content_markdown}\n\n## Rubric\n\n${draft.rubric}`;
      const shortText =
        objective.text.length > 60
          ? `${objective.text.slice(0, 60)}…`
          : objective.text;
      const item = await addItem(courseId, {
        title: `Assessment: ${shortText}`,
        kind: "assessment",
        content: body,
        week_index: objective.week_index ?? undefined,
      });
      // Link the new assessment item to the objective it measures.
      await alignItem(item.id, objective.id);
      return item;
    },
    onSuccess: () => {
      generateMutation.reset();
      onChanged();
    },
  });

  const draft = generateMutation.data;
  const aiNotConfigured = isAiNotConfigured(generateMutation.error);

  return (
    <Box
      data-testid="objective-card"
      sx={{
        p: 1,
        borderRadius: 1.5,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
      }}
    >
      <Typography variant="body2" sx={{ fontWeight: 500 }}>
        {objective.text}
      </Typography>
      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5, mt: 0.5 }}>
        <Chip
          label={objective.bloom_level}
          size="small"
          variant="outlined"
          sx={{ height: 20, fontSize: 11 }}
        />
        {objective.key_skills.map((s) => (
          <Chip
            key={s}
            label={s}
            size="small"
            sx={{ height: 20, fontSize: 11 }}
          />
        ))}
      </Box>

      <Button
        size="small"
        onClick={() => generateMutation.mutate()}
        disabled={generateMutation.isPending}
        data-testid="objective-generate-assessment-btn"
        startIcon={
          generateMutation.isPending ? (
            <CircularProgress size={12} />
          ) : undefined
        }
        sx={{ mt: 0.5 }}
      >
        ✨ Generate assessment
      </Button>

      {aiNotConfigured && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 0.25 }}
          data-testid="assessment-ai-not-configured"
        >
          AI not configured
        </Typography>
      )}
      {generateMutation.isError && !aiNotConfigured && (
        <Alert severity="error" sx={{ mt: 0.5 }}>
          Failed to generate assessment.
        </Alert>
      )}

      {draft && (
        <Box
          data-testid="assessment-draft"
          sx={{
            mt: 0.75,
            p: 1,
            borderRadius: 1.5,
            border: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ display: "block", mb: 0.5 }}
          >
            Assessment draft (advisory)
          </Typography>
          <MarkdownPreview text={draft.content_markdown} />
          <Typography
            variant="caption"
            sx={{ display: "block", mt: 0.75, fontWeight: 600 }}
          >
            Rubric
          </Typography>
          <MarkdownPreview text={draft.rubric} />
          <Caveats caveats={draft.caveats} />
          <Button
            size="small"
            variant="outlined"
            sx={{ mt: 0.75 }}
            disabled={applyMutation.isPending}
            onClick={() => applyMutation.mutate(draft)}
            data-testid="assessment-apply-btn"
          >
            {applyMutation.isPending ? "Adding…" : "Add as assessment item"}
          </Button>
        </Box>
      )}
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Objectives rail
// ---------------------------------------------------------------------------

function ObjectiveRail({
  courseId,
  objectives,
  onChanged,
}: {
  courseId: string;
  objectives: ObjectiveOut[];
  onChanged: () => void;
}) {
  const [text, setText] = useState("");
  const [bloom, setBloom] = useState<BloomLevel>("understand");
  const [skillsInput, setSkillsInput] = useState("");
  const [skills, setSkills] = useState<string[]>([]);
  const [week, setWeek] = useState("");

  const mutation = useMutation({
    mutationFn: (body: Parameters<typeof addObjective>[1]) =>
      addObjective(courseId, body),
    onSuccess: () => {
      setText("");
      setBloom("understand");
      setSkillsInput("");
      setSkills([]);
      setWeek("");
      onChanged();
    },
  });

  function commitSkill() {
    const v = skillsInput.trim();
    if (v && !skills.includes(v)) setSkills([...skills, v]);
    setSkillsInput("");
  }

  function handleSkillKey(e: KeyboardEvent) {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      commitSkill();
    }
  }

  function submit() {
    if (!text.trim()) return;
    // Fold any pending typed skill in before submitting.
    const pending = skillsInput.trim();
    const finalSkills =
      pending && !skills.includes(pending) ? [...skills, pending] : skills;
    const w = parseInt(week, 10);
    mutation.mutate({
      text: text.trim(),
      bloom_level: bloom,
      key_skills: finalSkills,
      week_index: Number.isNaN(w) ? undefined : w,
    });
  }

  // Group + sort by week (unscheduled — null/0 — trails at the end).
  const grouped = new Map<number, ObjectiveOut[]>();
  for (const o of objectives) {
    const k = o.week_index ?? 0;
    if (!grouped.has(k)) grouped.set(k, []);
    grouped.get(k)!.push(o);
  }
  const weeks = [...grouped.keys()].sort((a, b) => {
    const wa = a === 0 ? Infinity : a;
    const wb = b === 0 ? Infinity : b;
    return wa - wb;
  });

  return (
    <Paper variant="outlined" sx={{ p: 2 }} data-testid="objective-rail">
      <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1.5 }}>
        Objectives
      </Typography>

      <Stack spacing={1.5} data-testid="objective-add">
        <TextField
          label="Objective"
          value={text}
          onChange={(e) => setText(e.target.value)}
          size="small"
          multiline
          minRows={2}
          fullWidth
          slotProps={{ htmlInput: { "data-testid": "objective-text" } }}
        />
        {/* Bloom and Week are in separate rows so the Select has the full
            column width (~268px). When placed side-by-side at the 300px rail
            width, Bloom only gets ~164px — too narrow for translated values
            that expand ~40% (e.g. "understand" → 16-char bracket form). */}
        <TextField
          select
          label="Bloom"
          value={bloom}
          onChange={(e) => setBloom(e.target.value as BloomLevel)}
          size="small"
          fullWidth
        >
          {BLOOM_LEVELS.map((b) => (
            <MenuItem key={b} value={b}>
              {b}
            </MenuItem>
          ))}
        </TextField>
        <TextField
          label="Week"
          type="number"
          value={week}
          onChange={(e) => setWeek(e.target.value)}
          size="small"
          sx={{ width: 96 }}
          slotProps={{ htmlInput: { min: 0 } }}
        />

        <TextField
          label="Key skills (Enter or comma)"
          value={skillsInput}
          onChange={(e) => setSkillsInput(e.target.value)}
          onKeyDown={handleSkillKey}
          onBlur={commitSkill}
          size="small"
          fullWidth
        />
        {skills.length > 0 && (
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5 }}>
            {skills.map((s) => (
              <Chip
                key={s}
                label={s}
                size="small"
                onDelete={() => setSkills(skills.filter((x) => x !== s))}
              />
            ))}
          </Box>
        )}

        <Button
          variant="outlined"
          onClick={submit}
          disabled={!text.trim() || mutation.isPending}
          data-testid="add-objective-btn"
        >
          Add objective
        </Button>
      </Stack>

      <GenerateObjectivesPanel courseId={courseId} onChanged={onChanged} />

      <Divider sx={{ my: 2 }} />

      {objectives.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          No objectives yet.
        </Typography>
      ) : (
        <Stack spacing={1.5}>
          {weeks.map((w) => (
            <Box key={w}>
              <Chip
                label={weekLabel(w)}
                size="small"
                color="primary"
                sx={{ fontWeight: 600, mb: 0.75 }}
              />
              <Stack spacing={0.75}>
                {grouped.get(w)!.map((o) => (
                  <ObjectiveCard
                    key={o.id}
                    objective={o}
                    courseId={courseId}
                    onChanged={onChanged}
                  />
                ))}
              </Stack>
            </Box>
          ))}
        </Stack>
      )}
    </Paper>
  );
}

// ---------------------------------------------------------------------------
// Attached media (slice 2) — chips + a picker to attach a ready asset
// ---------------------------------------------------------------------------

function AttachedMedia({ itemId }: { itemId: string }) {
  const queryClient = useQueryClient();
  const [pick, setPick] = useState("");

  const attachedQuery = useQuery({
    queryKey: ["item-media", itemId],
    queryFn: () => listItemMedia(itemId),
  });
  const readyQuery = useQuery({
    queryKey: ["media-ready"],
    queryFn: listReadyMedia,
  });

  function refetch() {
    queryClient.invalidateQueries({ queryKey: ["item-media", itemId] });
  }

  const attach = useMutation({
    mutationFn: (assetId: string) => attachMedia(itemId, assetId),
    onSuccess: () => {
      setPick("");
      refetch();
    },
  });
  const detach = useMutation({
    mutationFn: (assetId: string) => detachMedia(itemId, assetId),
    onSuccess: refetch,
  });

  const attached = attachedQuery.data ?? [];
  const ready = readyQuery.data ?? [];
  const attachedIds = new Set(attached.map((m) => m.media_asset_id));
  const available = ready.filter((a) => !attachedIds.has(a.id));

  return (
    <Box sx={{ mt: 0.75 }} data-testid="item-media">
      {attached.length > 0 && (
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5, mb: 0.5 }}>
          {attached.map((m) => (
            <Chip
              key={m.media_asset_id}
              label={m.filename}
              size="small"
              variant="outlined"
              color="secondary"
              data-testid="item-media-chip"
              onDelete={
                detach.isPending
                  ? undefined
                  : () => detach.mutate(m.media_asset_id)
              }
              sx={{ height: 20, fontSize: 11 }}
            />
          ))}
        </Box>
      )}
      {available.length > 0 && (
        <TextField
          select
          label="Attach media"
          value={pick}
          onChange={(e) => {
            const id = e.target.value;
            setPick(id);
            if (id) attach.mutate(id);
          }}
          size="small"
          fullWidth
          disabled={attach.isPending}
          data-testid="item-media-attach"
        >
          <MenuItem value="">
            <em>Select an asset…</em>
          </MenuItem>
          {available.map((a) => (
            <MenuItem key={a.id} value={a.id}>
              {a.filename}
            </MenuItem>
          ))}
        </TextField>
      )}
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Item card (with optional AI categorize refinement)
// ---------------------------------------------------------------------------

function ItemCard({
  item,
  onChanged,
}: {
  item: ItemOut;
  onChanged: () => void;
}) {
  // Stateless AI categorize preview — does not mutate the item until Apply.
  const categorizeMutation = useMutation({
    mutationFn: () => categorizeItemAI(item.id),
  });
  const applyMutation = useMutation({
    mutationFn: (body: { kind: string; estimated_minutes: number }) =>
      updateItem(item.id, body),
    onSuccess: () => {
      categorizeMutation.reset();
      onChanged();
    },
  });

  // Advisory content generation — the draft body is returned, and only written
  // into the item via updateItem when the author clicks Apply.
  const generateContentMutation = useMutation({
    mutationFn: () => generateItemContent(item.id),
  });
  const applyContentMutation = useMutation({
    mutationFn: (markdown: string) =>
      updateItem(item.id, { content: markdown }),
    onSuccess: () => {
      generateContentMutation.reset();
      onChanged();
    },
  });

  const suggestion = categorizeMutation.data;
  const contentDraft = generateContentMutation.data;
  const aiNotConfigured =
    isAiNotConfigured(categorizeMutation.error) ||
    isAiNotConfigured(generateContentMutation.error);

  return (
    <Box
      data-testid="item-card"
      sx={{
        p: 1.5,
        borderRadius: 2,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
      }}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          gap: 1,
          mb: 0.5,
        }}
      >
        <Chip
          label={kindLabel(item.kind)}
          size="small"
          variant="outlined"
          sx={{ height: 22, fontSize: 11 }}
        />
        <Chip
          label={weekLabel(item.week_index)}
          size="small"
          sx={{ height: 22, fontSize: 11 }}
        />
      </Box>
      <Typography variant="body2" sx={{ fontWeight: 600 }}>
        {item.title}
      </Typography>
      {item.estimated_minutes != null && (
        <Typography variant="caption" color="text.secondary">
          ~{item.estimated_minutes} min student effort
        </Typography>
      )}

      <AttachedMedia itemId={item.id} />

      <Box sx={{ mt: 0.75, display: "flex", flexWrap: "wrap", gap: 0.5 }}>
        <Button
          size="small"
          onClick={() => categorizeMutation.mutate()}
          disabled={categorizeMutation.isPending}
          data-testid="item-ai-categorize-btn"
          startIcon={
            categorizeMutation.isPending ? (
              <CircularProgress size={12} />
            ) : undefined
          }
        >
          ✨ AI
        </Button>
        <Button
          size="small"
          onClick={() => generateContentMutation.mutate()}
          disabled={generateContentMutation.isPending}
          data-testid="item-generate-content-btn"
          startIcon={
            generateContentMutation.isPending ? (
              <CircularProgress size={12} />
            ) : undefined
          }
        >
          ✨ Generate content
        </Button>
      </Box>

      {aiNotConfigured && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 0.5 }}
          data-testid="ai-not-configured"
        >
          AI not configured
        </Typography>
      )}

      {suggestion && (
        <Box
          data-testid="item-ai-suggestion"
          sx={{
            mt: 0.75,
            p: 1,
            borderRadius: 1.5,
            border: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5 }}>
            <Chip
              label={kindLabel(suggestion.kind)}
              size="small"
              color="primary"
              variant="outlined"
              sx={{ height: 20, fontSize: 11 }}
            />
            <Chip
              label={`~${suggestion.estimated_minutes} min`}
              size="small"
              sx={{ height: 20, fontSize: 11 }}
            />
          </Box>
          <Typography variant="caption" sx={{ display: "block", mt: 0.5 }}>
            {suggestion.served_objective_hint}
          </Typography>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ display: "block", mt: 0.25 }}
          >
            {suggestion.rationale}
          </Typography>
          <Button
            size="small"
            variant="outlined"
            sx={{ mt: 0.75 }}
            disabled={applyMutation.isPending}
            onClick={() =>
              applyMutation.mutate({
                kind: suggestion.kind,
                estimated_minutes: suggestion.estimated_minutes,
              })
            }
            data-testid="item-ai-apply-btn"
          >
            {applyMutation.isPending ? "Applying…" : "Apply"}
          </Button>
        </Box>
      )}

      {contentDraft && (
        <Box
          data-testid="item-content-draft"
          sx={{
            mt: 0.75,
            p: 1,
            borderRadius: 1.5,
            border: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ display: "block", mb: 0.5 }}
          >
            {contentDraft.summary}
          </Typography>
          <MarkdownPreview text={contentDraft.content_markdown} />
          <Caveats caveats={contentDraft.caveats} />
          <Button
            size="small"
            variant="outlined"
            sx={{ mt: 0.75 }}
            disabled={applyContentMutation.isPending}
            onClick={() =>
              applyContentMutation.mutate(contentDraft.content_markdown)
            }
            data-testid="item-content-apply-btn"
          >
            {applyContentMutation.isPending ? "Applying…" : "Apply to item"}
          </Button>
        </Box>
      )}
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Quick-capture + item list
// ---------------------------------------------------------------------------

function CaptureColumn({
  courseId,
  objectives,
  items,
  onChanged,
}: {
  courseId: string;
  objectives: ObjectiveOut[];
  items: ItemOut[];
  onChanged: () => void;
}) {
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [week, setWeek] = useState("");
  const [objectiveId, setObjectiveId] = useState("");

  const readyMediaQuery = useQuery({
    queryKey: ["media-ready"],
    queryFn: listReadyMedia,
  });
  const readyMedia = readyMediaQuery.data ?? [];

  function insertMediaRef(assetId: string) {
    setContent((c) =>
      c && !c.endsWith("\n") ? `${c}\n![[media:${assetId}]]` : `${c}![[media:${assetId}]]`
    );
  }

  const mutation = useMutation({
    mutationFn: async () => {
      const w = parseInt(week, 10);
      const item = await addItem(courseId, {
        title: title.trim(),
        content: content.trim() || undefined,
        week_index: Number.isNaN(w) ? undefined : w,
      });
      if (objectiveId) await alignItem(item.id, objectiveId);
      return item;
    },
    onSuccess: () => {
      setTitle("");
      setContent("");
      // keep week + objective selection for fast repeated capture
      onChanged();
    },
  });

  function submit() {
    if (!title.trim() || mutation.isPending) return;
    mutation.mutate();
  }

  function handleKey(e: KeyboardEvent) {
    // Enter (without Shift) on the title field submits.
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <Stack spacing={2}>
      <Paper
        variant="outlined"
        sx={{ p: 2 }}
        data-testid="item-capture"
      >
        <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1.5 }}>
          Quick-capture content
        </Typography>
        <Stack spacing={1.5}>
          <TextField
            label="Title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={handleKey}
            size="small"
            fullWidth
            slotProps={{ htmlInput: { "data-testid": "item-title" } }}
          />
          <TextField
            label="Content (paste — kind is auto-detected)"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            size="small"
            multiline
            minRows={3}
            fullWidth
            slotProps={{ htmlInput: { "data-testid": "item-content" } }}
          />
          {readyMedia.length > 0 && (
            <TextField
              select
              label="Insert media embed"
              value=""
              onChange={(e) => {
                if (e.target.value) insertMediaRef(e.target.value);
              }}
              size="small"
              fullWidth
              data-testid="insert-media"
              helperText="Adds an ![[media:…]] reference to the content above"
            >
              <MenuItem value="">
                <em>Insert an asset reference…</em>
              </MenuItem>
              {readyMedia.map((a) => (
                <MenuItem key={a.id} value={a.id}>
                  {a.filename}
                </MenuItem>
              ))}
            </TextField>
          )}
          <Stack direction="row" spacing={1}>
            <TextField
              label="Week"
              type="number"
              value={week}
              onChange={(e) => setWeek(e.target.value)}
              size="small"
              sx={{ width: 96 }}
              slotProps={{ htmlInput: { min: 0 } }}
            />
            <TextField
              select
              label="Attach to objective"
              value={objectiveId}
              onChange={(e) => setObjectiveId(e.target.value)}
              size="small"
              sx={{ flex: 1 }}
            >
              <MenuItem value="">
                <em>None</em>
              </MenuItem>
              {objectives.map((o) => (
                <MenuItem key={o.id} value={o.id}>
                  {o.text.length > 48 ? `${o.text.slice(0, 48)}…` : o.text}
                </MenuItem>
              ))}
            </TextField>
          </Stack>

          {mutation.isError && (
            <Alert severity="error">Failed to add the item.</Alert>
          )}

          <Box>
            <Button
              variant="contained"
              onClick={submit}
              disabled={!title.trim() || mutation.isPending}
              data-testid="add-item-btn"
            >
              {mutation.isPending ? "Adding…" : "Add item"}
            </Button>
          </Box>
        </Stack>
      </Paper>

      <Box>
        <Typography variant="subtitle2" sx={{ mb: 1 }}>
          Items ({items.length})
        </Typography>
        {items.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No items yet. Paste some content above to get started.
          </Typography>
        ) : (
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: { xs: "1fr", sm: "repeat(2, 1fr)" },
              gap: 1.5,
            }}
          >
            {items.map((item) => (
              <ItemCard key={item.id} item={item} onChanged={onChanged} />
            ))}
          </Box>
        )}
      </Box>
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// Effort / overload meter
// ---------------------------------------------------------------------------

function EffortMeter({ rows }: { rows: OverloadWeek[] }) {
  const scheduled = rows.filter((r) => r.week > 0);
  const unscheduled = rows.filter((r) => r.week === 0);
  const ordered = [...scheduled, ...unscheduled];

  return (
    <Paper variant="outlined" sx={{ p: 2 }} data-testid="effort-meter">
      <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1.5 }}>
        Weekly load
      </Typography>
      {ordered.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          Add scheduled items to see weekly student effort.
        </Typography>
      ) : (
        <Stack spacing={1.25}>
          {ordered.map((r) => (
            <Box
              key={r.week}
              data-testid="effort-week"
              sx={{
                p: 1,
                borderRadius: 1.5,
                border: "1px solid",
                borderColor: r.overload ? "warning.main" : "divider",
                bgcolor: "background.paper",
              }}
            >
              <Box
                sx={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <Typography variant="body2" sx={{ fontWeight: 600 }}>
                  {weekLabel(r.week)}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {r.student_hours.toFixed(1)} h
                </Typography>
              </Box>
              <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5, mt: 0.5 }}>
                {r.overload && (
                  <Chip
                    label="Overload"
                    size="small"
                    color="warning"
                    sx={{ height: 20, fontSize: 11 }}
                  />
                )}
                {r.density_warn && (
                  <Chip
                    label={`${r.new_concepts} new concepts`}
                    size="small"
                    color="warning"
                    variant="outlined"
                    sx={{ height: 20, fontSize: 11 }}
                  />
                )}
              </Box>
            </Box>
          ))}
        </Stack>
      )}
    </Paper>
  );
}

// ---------------------------------------------------------------------------
// Canvas shell
// ---------------------------------------------------------------------------

export function ObjectiveCanvas({ course }: { course: CourseOut }) {
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
  const overloadQuery = useQuery({
    queryKey: ["builder-overload", courseId],
    queryFn: () => getOverload(courseId),
  });

  function invalidateAll() {
    queryClient.invalidateQueries({ queryKey: ["builder-objectives", courseId] });
    queryClient.invalidateQueries({ queryKey: ["builder-items", courseId] });
    queryClient.invalidateQueries({ queryKey: ["builder-effort", courseId] });
    queryClient.invalidateQueries({ queryKey: ["builder-overload", courseId] });
  }

  const objectives = objectivesQuery.data ?? [];
  const items = itemsQuery.data ?? [];
  const overload = overloadQuery.data ?? [];

  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: { xs: "1fr", md: "300px 1fr 260px" },
        gap: 2,
        alignItems: "start",
      }}
    >
      <ObjectiveRail
        courseId={courseId}
        objectives={objectives}
        onChanged={invalidateAll}
      />
      <CaptureColumn
        courseId={courseId}
        objectives={objectives}
        items={items}
        onChanged={invalidateAll}
      />
      <EffortMeter rows={overload} />
    </Box>
  );
}
