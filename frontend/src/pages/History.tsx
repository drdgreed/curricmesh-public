import { useQuery } from "@tanstack/react-query";
import Box from "@mui/material/Box";
import CircularProgress from "@mui/material/CircularProgress";
import Divider from "@mui/material/Divider";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Alert from "@mui/material/Alert";
import Chip from "@mui/material/Chip";
import { getDashboard } from "../api/client";
import type { RecentEvent } from "../api/client";

// Humanized labels for known event types. Anything unmapped falls back to a
// Title-cased version of the raw event_type.
const EVENT_TYPE_LABELS: Record<string, string> = {
  version_active: "Version activated",
  version_approved: "Version approved",
  version_draft: "Version drafted",
  version_review: "Sent to review",
  version_archived: "Version archived",
  ccr_created: "Change request opened",
  qa_passed: "QA passed",
};

function humanizeEventType(eventType: string): string {
  const mapped = EVENT_TYPE_LABELS[eventType];
  if (mapped) return mapped;
  return eventType
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

function EventRow({ event }: { event: RecentEvent }) {
  const when = new Date(event.created_at).toLocaleString();
  const actor = event.actor_label ?? "System";
  const target = event.target_label ?? event.target;

  return (
    <Box sx={{ py: 1.5 }}>
      <Stack direction="row" spacing={1.5} sx={{ alignItems: "flex-start", flexWrap: "wrap" }}>
        <Chip label={humanizeEventType(event.event_type)} size="small" variant="outlined" />
        <Box sx={{ flex: 1 }}>
          <Typography variant="body2">
            <strong>{actor}</strong> &middot; {target}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {when}
          </Typography>
        </Box>
      </Stack>
    </Box>
  );
}

export function History() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["dashboard"],
    queryFn: getDashboard,
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
      <Alert severity="error">
        Failed to load history. Please try again.
      </Alert>
    );
  }

  const events = data.recent_events;

  return (
    <Box>
      <Typography variant="h5">Audit History</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        An immutable, chronological log of every change across your organization.
      </Typography>
      <Paper variant="outlined">
        {events.length === 0 ? (
          <Box sx={{ p: 3 }}>
            <Typography color="text.secondary">No events recorded yet.</Typography>
          </Box>
        ) : (
          <Box sx={{ px: 2 }}>
            {events.map((evt, idx) => (
              <Box key={evt.id}>
                <EventRow event={evt} />
                {idx < events.length - 1 && <Divider />}
              </Box>
            ))}
          </Box>
        )}
      </Paper>
    </Box>
  );
}
