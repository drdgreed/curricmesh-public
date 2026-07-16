import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import Divider from "@mui/material/Divider";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

interface Placement {
  target_kind: string;
  target_ref: string | null;
  position_hint: string;
  rationale: string;
  confidence: number;
}

interface SampleAssessment {
  stem: string;
  kind: string;
  answer_or_rubric: string;
}

interface DraftFrame {
  outline: string[];
  sample_assessments: SampleAssessment[];
  caveats: string[];
}

export interface Enrichment {
  placement: Placement;
  draft_frame: DraftFrame;
  generated_at?: string;
}

export function EnrichmentBlock({ enrichment }: { enrichment: Enrichment }) {
  const { placement: p, draft_frame: d } = enrichment;
  return (
    <>
      <Divider sx={{ my: 1.5 }} />
      <Stack direction="row" spacing={1} sx={{ alignItems: "center", mb: 0.5 }}>
        <Typography variant="subtitle2">Placement</Typography>
        <Chip
          size="small"
          label={`${p.target_kind}${p.target_ref ? ` · ${p.target_ref}` : ""}`}
        />
        <Chip
          size="small"
          variant="outlined"
          label={`confidence ${(p.confidence * 100).toFixed(0)}%`}
        />
      </Stack>
      <Typography variant="body2">{p.position_hint}</Typography>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block" }}
      >
        {p.rationale}
      </Typography>

      <Typography variant="subtitle2" sx={{ mt: 1 }}>
        Draft outline
      </Typography>
      <Stack component="ul" spacing={0.25} sx={{ pl: 2, my: 0.5 }}>
        {d.outline.map((o, i) => (
          <Typography key={i} component="li" variant="body2">
            {o}
          </Typography>
        ))}
      </Stack>

      {d.sample_assessments.length > 0 && (
        <Box sx={{ mt: 0.5 }}>
          <Typography variant="subtitle2">Sample assessment</Typography>
          {d.sample_assessments.map((s, i) => (
            <Typography key={i} variant="body2" sx={{ mb: 0.5 }}>
              <b>[{s.kind}]</b> {s.stem} — <i>{s.answer_or_rubric}</i>
            </Typography>
          ))}
        </Box>
      )}

      {d.caveats.length > 0 && (
        <Typography
          variant="caption"
          color="warning.main"
          sx={{ display: "block", mt: 0.5 }}
        >
          Verify: {d.caveats.join("; ")}
        </Typography>
      )}
    </>
  );
}
