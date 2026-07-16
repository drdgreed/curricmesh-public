import { useQuery } from "@tanstack/react-query";
import Box from "@mui/material/Box";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import CircularProgress from "@mui/material/CircularProgress";
import Divider from "@mui/material/Divider";
import LinearProgress from "@mui/material/LinearProgress";
import Stack from "@mui/material/Stack";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import Typography from "@mui/material/Typography";
import Alert from "@mui/material/Alert";
import { getAnalyticsOverview } from "../api/client";
import type {
  AnalyticsOverview,
  StateCount,
  StateDuration,
  VelocityBucket,
} from "../api/client";
import { StatusBadge } from "../components/StatusBadge";

// React Query hook for the composed analytics payload. Kept here (not in a
// hooks/ dir) to match the page-local useQuery convention in Dashboard.tsx.
export function useAnalyticsOverview() {
  return useQuery<AnalyticsOverview>({
    queryKey: ["analytics", "overview"],
    queryFn: getAnalyticsOverview,
  });
}

function fmtDays(d: number | null): string {
  return d === null ? "—" : `${d.toFixed(1)}d`;
}

function fmtBucket(iso: string): string {
  // ISO datetime → "YYYY-MM-DD" (the bucket start date is what matters).
  return iso.slice(0, 10);
}

function SummaryCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card variant="outlined" sx={{ flex: 1, minWidth: 160 }}>
      <CardContent>
        <Typography variant="overline" color="text.secondary">
          {label}
        </Typography>
        <Typography variant="h4">{value}</Typography>
        {hint && (
          <Typography variant="caption" color="text.secondary">
            {hint}
          </Typography>
        )}
      </CardContent>
    </Card>
  );
}

function VelocityChart({ buckets }: { buckets: VelocityBucket[] }) {
  if (buckets.length === 0) {
    return (
      <Typography variant="body2" color="text.secondary">
        No change activity yet.
      </Typography>
    );
  }
  // Scale bar widths to the largest single count across both series.
  const max = Math.max(
    1,
    ...buckets.map((b) => Math.max(b.ccrs_opened, b.versions_released))
  );
  return (
    <Stack spacing={1.5}>
      {buckets.map((b) => (
        <Box key={b.bucket_start}>
          <Typography variant="caption" sx={{ fontFamily: "monospace" }}>
            {fmtBucket(b.bucket_start)}
          </Typography>
          <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
            <Box sx={{ flex: 1 }}>
              <LinearProgress
                variant="determinate"
                value={(b.ccrs_opened / max) * 100}
                sx={{ height: 10, borderRadius: 1 }}
                data-testid={`velocity-ccrs-${fmtBucket(b.bucket_start)}`}
              />
            </Box>
            <Typography variant="caption" sx={{ minWidth: 130 }}>
              {b.ccrs_opened} opened · {b.versions_released} released
            </Typography>
          </Stack>
        </Box>
      ))}
    </Stack>
  );
}

function TimeInStateTable({ rows }: { rows: StateDuration[] }) {
  return (
    <Table size="small">
      <TableHead>
        <TableRow>
          <TableCell>State</TableCell>
          <TableCell align="right">n</TableCell>
          <TableCell align="right">Mean</TableCell>
          <TableCell align="right">Median</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {rows.map((r) => (
          <TableRow key={r.state}>
            <TableCell>
              <StatusBadge status={r.state} />
            </TableCell>
            <TableCell align="right">{r.n}</TableCell>
            <TableCell align="right">
              {r.n === 0 ? (
                <Typography variant="caption" color="text.secondary">
                  no data
                </Typography>
              ) : (
                fmtDays(r.mean_days)
              )}
            </TableCell>
            <TableCell align="right">
              {r.n === 0 ? "—" : fmtDays(r.median_days)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function Distribution({ rows }: { rows: StateCount[] }) {
  if (rows.length === 0) {
    return (
      <Typography variant="body2" color="text.secondary">
        Nothing to distribute yet.
      </Typography>
    );
  }
  return (
    <Stack spacing={1}>
      {rows.map((r) => (
        <Stack
          key={`${r.entity}-${r.status}`}
          direction="row"
          spacing={1}
          sx={{ alignItems: "center" }}
        >
          <Typography variant="body2" sx={{ minWidth: 70, textTransform: "capitalize" }}>
            {r.entity}
          </Typography>
          <StatusBadge status={r.status} />
          <Typography variant="body2" color="text.secondary">
            × {r.count}
          </Typography>
        </Stack>
      ))}
    </Stack>
  );
}

export function Analytics() {
  const { data, isLoading, isError } = useAnalyticsOverview();

  if (isLoading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", mt: 8 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (isError || !data) {
    return (
      <Alert severity="error">Failed to load analytics. Please try again.</Alert>
    );
  }

  const totalOpened = data.velocity.reduce((s, b) => s + b.ccrs_opened, 0);
  const totalReleased = data.velocity.reduce((s, b) => s + b.versions_released, 0);
  const hasAnyData =
    data.velocity.length > 0 ||
    data.distribution.length > 0 ||
    data.cadence.releases > 0;

  return (
    <Box>
      <Typography variant="h5">Analytics</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Change velocity and time-in-state metrics across your curriculum.
      </Typography>

      {!hasAnyData ? (
        <Typography color="text.secondary">
          No analytics data yet — open a change request or release a version to
          see velocity and time-in-state metrics.
        </Typography>
      ) : (
        <Stack spacing={3}>
          <Stack direction="row" spacing={2} sx={{ flexWrap: "wrap" }}>
            <SummaryCard label="CCRs opened" value={String(totalOpened)} />
            <SummaryCard label="Versions released" value={String(totalReleased)} />
            <SummaryCard
              label="Release cadence"
              value={fmtDays(data.cadence.mean_days_between)}
              hint={`${data.cadence.releases} release${
                data.cadence.releases === 1 ? "" : "s"
              } · median ${fmtDays(data.cadence.median_days_between)}`}
            />
          </Stack>

          <Card variant="outlined">
            <CardContent>
              <Typography variant="subtitle1" gutterBottom>
                Change velocity
              </Typography>
              <Divider sx={{ mb: 2 }} />
              <VelocityChart buckets={data.velocity} />
            </CardContent>
          </Card>

          <Card variant="outlined">
            <CardContent>
              <Typography variant="subtitle1" gutterBottom>
                Time in state
              </Typography>
              <Typography variant="caption" color="text.secondary">
                Dwell time reconstructed from version lifecycle transitions.
                States with no completed interval report "no data" rather than a
                fabricated duration.
              </Typography>
              <Divider sx={{ my: 2 }} />
              <TimeInStateTable rows={data.time_in_state} />
            </CardContent>
          </Card>

          <Card variant="outlined">
            <CardContent>
              <Typography variant="subtitle1" gutterBottom>
                Current distribution
              </Typography>
              <Divider sx={{ mb: 2 }} />
              <Distribution rows={data.distribution} />
            </CardContent>
          </Card>
        </Stack>
      )}
    </Box>
  );
}
