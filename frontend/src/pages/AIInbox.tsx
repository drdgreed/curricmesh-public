import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Box from "@mui/material/Box";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Divider from "@mui/material/Divider";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import { getAIInbox, submitQAReview, enrichCCR } from "../api/client";
import type { AICCRDraft, AIDraftQA } from "../api/client";
import { EnrichmentBlock } from "../components/EnrichmentBlock";
import type { Enrichment } from "../components/EnrichmentBlock";
import { useAuth } from "../auth/AuthContext";

const PROMOTE_ROLES = new Set(["qa_lead", "architect"]);

function AICCRCard({ ccr }: { ccr: AICCRDraft }) {
  const research = ccr.impact?.ai_research as
    | { topic?: string; coverage_status?: string; citations?: string[] }
    | undefined;
  const enrichment = ccr.impact?.enrichment as Enrichment | undefined;

  const qc = useQueryClient();
  const enrich = useMutation({
    mutationFn: () => enrichCCR(ccr.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ai-inbox"] }),
  });

  return (
    <Card variant="outlined">
      <CardContent>
        <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
          <Typography variant="h6">{ccr.title}</Typography>
          {ccr.proposed_bump && (
            <Chip label={ccr.proposed_bump} size="small" color="primary" variant="outlined" />
          )}
        </Stack>

        {ccr.rationale && (
          <Typography variant="body2" sx={{ mt: 1 }}>
            {ccr.rationale}
          </Typography>
        )}

        {research && (
          <>
            <Divider sx={{ my: 1.5 }} />
            {research.topic && (
              <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                Topic: {research.topic}
              </Typography>
            )}
            {research.coverage_status && (
              <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                Coverage: {research.coverage_status}
              </Typography>
            )}
            {research.citations && research.citations.length > 0 && (
              <Box sx={{ mt: 1 }}>
                <Typography variant="subtitle2">Citations</Typography>
                <Stack component="ul" spacing={0.25} sx={{ pl: 2, my: 0.5 }}>
                  {research.citations.map((c, i) => (
                    <Typography key={i} component="li" variant="body2">
                      {c}
                    </Typography>
                  ))}
                </Stack>
              </Box>
            )}
            {enrichment ? (
              <EnrichmentBlock enrichment={enrichment} />
            ) : (
              <Button
                size="small"
                sx={{ mt: 1 }}
                disabled={enrich.isPending}
                onClick={() => enrich.mutate()}
              >
                {enrich.isPending ? "Enriching…" : "Enrich"}
              </Button>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function AIDraftQACard({ qa, canPromote }: { qa: AIDraftQA; canPromote: boolean }) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => submitQAReview(qa.ccr_id, qa.dimension_scores ?? {}, "pass"),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ai-inbox"] });
    },
  });

  const scores = qa.dimension_scores ?? {};
  const evidence = qa.evidence ?? {};

  return (
    <Card variant="outlined">
      <CardContent>
        <Typography variant="h6">{qa.ccr_title ?? "(untitled CCR)"}</Typography>

        <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap", gap: 1, mt: 1 }}>
          {Object.entries(scores).map(([dim, score]) => (
            <Chip key={dim} label={`${dim}: ${score}`} size="small" />
          ))}
        </Stack>

        {Object.keys(evidence).length > 0 && (
          <>
            <Divider sx={{ my: 1.5 }} />
            <Stack spacing={0.5}>
              {Object.entries(evidence).map(([dim, text]) => (
                <Typography key={dim} variant="body2">
                  <strong>{dim}:</strong> {text}
                </Typography>
              ))}
            </Stack>
          </>
        )}

        {canPromote && (
          <Box sx={{ mt: 2 }}>
            <Button
              variant="contained"
              size="small"
              onClick={() => mutation.mutate()}
              disabled={
                mutation.isPending ||
                !qa.dimension_scores ||
                Object.keys(qa.dimension_scores).length === 0
              }
            >
              Accept scores
            </Button>
            {mutation.isError && (
              <Alert severity="error" sx={{ mt: 1 }}>
                Failed to submit QA review. Please try again.
              </Alert>
            )}
          </Box>
        )}
      </CardContent>
    </Card>
  );
}

export function AIInbox() {
  const { role } = useAuth();
  const canPromote = role != null && PROMOTE_ROLES.has(role);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["ai-inbox"],
    queryFn: getAIInbox,
  });

  if (isLoading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", mt: 8 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (isError || !data) {
    return <Alert severity="error">Failed to load AI inbox. Please try again.</Alert>;
  }

  return (
    <Box>
      <Typography variant="h5">AI Findings Inbox</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        AI-drafted change requests and quality pre-scores awaiting your review.
      </Typography>

      <Typography variant="h6" sx={{ mt: 2 }} gutterBottom>
        AI-Drafted Change Requests
      </Typography>
      {data.drafted_ccrs.length === 0 ? (
        <Typography color="text.secondary">No AI-drafted change requests.</Typography>
      ) : (
        <Stack spacing={2}>
          {data.drafted_ccrs.map((c) => (
            <AICCRCard key={c.id} ccr={c} />
          ))}
        </Stack>
      )}

      <Typography variant="h6" sx={{ mt: 4 }} gutterBottom>
        AI QA Pre-Scores
      </Typography>
      {data.draft_qa_reviews.length === 0 ? (
        <Typography color="text.secondary">No AI QA pre-scores.</Typography>
      ) : (
        <Stack spacing={2}>
          {data.draft_qa_reviews.map((qa) => (
            <AIDraftQACard key={qa.id} qa={qa} canPromote={canPromote} />
          ))}
        </Stack>
      )}
    </Box>
  );
}
