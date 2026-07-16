import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Accordion from "@mui/material/Accordion";
import AccordionDetails from "@mui/material/AccordionDetails";
import AccordionSummary from "@mui/material/AccordionSummary";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Collapse from "@mui/material/Collapse";
import Divider from "@mui/material/Divider";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Button from "@mui/material/Button";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import { listAssessments } from "../api/client";
import type { AssessmentOut } from "../api/client";

// ---------------------------------------------------------------------------
// Per-card component
// ---------------------------------------------------------------------------

function AssessmentCard({ row, muted }: { row: AssessmentOut; muted?: boolean }) {
  const [expanded, setExpanded] = useState(false);

  const prevConfidence = typeof row.scores.prev_confidence === "number"
    ? row.scores.prev_confidence
    : null;
  const delta = prevConfidence !== null ? row.confidence - prevConfidence : null;

  const modelRec = typeof row.scores.model_recommendation === "string"
    ? (row.scores.model_recommendation as string)
    : null;

  return (
    <Card variant="outlined" sx={{ opacity: muted ? 0.7 : 1 }}>
      <CardContent>
        <Stack direction="row" spacing={1} sx={{ alignItems: "center", flexWrap: "wrap", gap: 1 }}>
          <Typography variant="h6" sx={{ flex: 1 }}>
            {row.display_topic}
          </Typography>

          {/* Confidence chip with delta */}
          <Stack direction="row" spacing={0.5} sx={{ alignItems: "center" }}>
            <Chip
              label={`${(row.confidence * 100).toFixed(0)}%`}
              size="small"
              color={row.confidence >= 0.7 ? "success" : row.confidence >= 0.4 ? "warning" : "default"}
              variant="outlined"
            />
            {delta !== null && Math.abs(delta) >= 0.001 && (
              <Typography
                variant="caption"
                sx={{ color: delta > 0 ? "success.main" : "error.main", fontWeight: 600 }}
              >
                {delta > 0 ? "▲" : "▼"}{(Math.abs(delta) * 100).toFixed(0)}%
              </Typography>
            )}
          </Stack>

          {/* Times-seen chip */}
          <Chip
            label={`seen ${row.times_seen}×`}
            size="small"
            variant="outlined"
          />
        </Stack>

        {/* Promoted CCR link area — only for adopt_now rows */}
        {row.recommendation === "adopt_now" && row.promoted_ccr_id && (
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
            CCR: {row.promoted_ccr_id}{" "}
            <Typography component="span" variant="caption" color="primary.main">
              (in AI Inbox)
            </Typography>
          </Typography>
        )}

        {/* Expand/collapse details */}
        <Box sx={{ mt: 1 }}>
          <Button
            size="small"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            sx={{ p: 0, minWidth: 0, textTransform: "none", color: "text.secondary" }}
          >
            {expanded ? "Hide details ▲" : "Show details ▼"}
          </Button>

          <Collapse in={expanded}>
            <Divider sx={{ my: 1 }} />

            {/* Rationale */}
            <Typography variant="body2" sx={{ mb: 1 }}>
              {row.rationale}
            </Typography>

            {/* Model recommendation badge when it differs from stored */}
            {modelRec && modelRec !== row.recommendation && (
              <Chip
                label={`model said: ${modelRec}`}
                size="small"
                color="warning"
                variant="outlined"
                sx={{ mb: 1 }}
              />
            )}

            {/* Dossier sightings */}
            {row.dossier.length > 0 && (
              <>
                <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
                  Evidence sightings ({row.dossier.length})
                </Typography>
                <Stack spacing={1}>
                  {row.dossier.map((sighting, i) => (
                    <Box key={i} sx={{ pl: 1, borderLeft: "2px solid", borderColor: "divider" }}>
                      <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                        {sighting.run_date}
                        {sighting.source_kinds?.length > 0 && ` · ${sighting.source_kinds.join(", ")}`}
                      </Typography>
                      {Array.isArray(sighting.evidence) && sighting.evidence.length > 0 && (
                        <Stack component="ul" spacing={0.25} sx={{ pl: 2, my: 0.25 }}>
                          {(sighting.evidence as string[]).map((ev, j) => (
                            <Typography key={j} component="li" variant="body2">
                              {ev}
                            </Typography>
                          ))}
                        </Stack>
                      )}
                    </Box>
                  ))}
                </Stack>
              </>
            )}
          </Collapse>
        </Box>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function MonitorQueue() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["freshness-assessments"],
    queryFn: () => listAssessments(),
  });

  if (isLoading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", mt: 8 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (isError || !data) {
    return (
      <Alert severity="error">Failed to load monitor queue. Please try again.</Alert>
    );
  }

  const monitoring = data.filter((r) => r.recommendation === "monitor");
  const rejected = data.filter((r) => r.recommendation === "reject");
  const promoted = data.filter((r) => r.recommendation === "adopt_now");

  return (
    <Box>
      <Typography variant="h5" gutterBottom>
        Monitor Queue
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Gaps accumulating evidence before promotion — monitored, rejected, and promoted assessments.
      </Typography>

      {/* Monitoring section — default expanded */}
      <Typography variant="h6" gutterBottom>
        Monitoring
      </Typography>
      {monitoring.length === 0 ? (
        <Typography color="text.secondary" sx={{ mb: 3 }}>
          Nothing monitored yet.
        </Typography>
      ) : (
        <Stack spacing={2} sx={{ mb: 4 }}>
          {monitoring.map((row) => (
            <AssessmentCard key={row.id} row={row} />
          ))}
        </Stack>
      )}

      {/* Promoted section */}
      <Typography variant="h6" gutterBottom>
        Promoted
      </Typography>
      {promoted.length === 0 ? (
        <Typography color="text.secondary" sx={{ mb: 3 }}>
          No promoted gaps yet.
        </Typography>
      ) : (
        <Stack spacing={2} sx={{ mb: 4 }}>
          {promoted.map((row) => (
            <AssessmentCard key={row.id} row={row} />
          ))}
        </Stack>
      )}

      {/* Rejected section — collapsed by default */}
      <Accordion defaultExpanded={false} disableGutters sx={{ mt: 2 }}>
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Typography variant="h6">
            Rejected{rejected.length > 0 && ` (${rejected.length})`}
          </Typography>
        </AccordionSummary>
        <AccordionDetails>
          {rejected.length === 0 ? (
            <Typography color="text.secondary">No rejected gaps.</Typography>
          ) : (
            <Stack spacing={2}>
              {rejected.map((row) => (
                <AssessmentCard key={row.id} row={row} muted />
              ))}
            </Stack>
          )}
        </AccordionDetails>
      </Accordion>
    </Box>
  );
}
