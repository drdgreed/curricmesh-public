import { useQuery } from "@tanstack/react-query";
import Box from "@mui/material/Box";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import CircularProgress from "@mui/material/CircularProgress";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { getAiUsage } from "../api/client";

/**
 * Compact dashboard tile showing the durable (persisted) AI spend for the
 * viewer's org. Renders nothing on error/403 — it must fail silently rather
 * than show a scary error on the dashboard (it's only mounted for author roles,
 * so a 403 shouldn't normally happen, but we're defensive).
 */
export function AiSpendTile() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["ai-usage"],
    queryFn: getAiUsage,
    retry: false,
  });

  if (isLoading) {
    return (
      <Card variant="outlined">
        <CardContent>
          <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
            <CircularProgress size={24} />
          </Box>
        </CardContent>
      </Card>
    );
  }

  // Fail silently on error/403 or missing payload.
  if (isError || !data?.persisted) return null;

  const p = data.persisted;
  const tokens = p.total_input_tokens + p.total_output_tokens;
  const maxCost = p.by_day.reduce((m, d) => Math.max(m, d.cost_usd), 0);

  // Top model by cost (optional breakdown line).
  const topModel = Object.entries(p.by_model).sort(
    (a, b) => b[1].cost_usd - a[1].cost_usd
  )[0];

  return (
    <Card variant="outlined" data-testid="ai-spend-tile">
      <CardContent>
        <Typography variant="subtitle2" color="text.secondary" gutterBottom>
          AI spend (this org)
        </Typography>

        <Typography variant="h4" data-testid="ai-spend-total">
          ${p.total_cost_usd.toFixed(2)}
        </Typography>

        <Typography variant="body2" color="text.secondary">
          {p.total_calls} {p.total_calls === 1 ? "call" : "calls"} ·{" "}
          {tokens.toLocaleString()} tokens
        </Typography>

        {topModel && (
          <Typography variant="caption" color="text.secondary">
            Top model: {topModel[0]} (${topModel[1].cost_usd.toFixed(2)})
          </Typography>
        )}

        <Box sx={{ mt: 1.5 }}>
          {p.by_day.length === 0 ? (
            <Typography variant="caption" color="text.secondary">
              No spend recorded yet.
            </Typography>
          ) : (
            <Stack
              direction="row"
              spacing={0.5}
              sx={{ alignItems: "flex-end", height: 40 }}
            >
              {p.by_day.map((d) => {
                // Height ∝ this day's cost relative to the 14-day max; tiny
                // floor so zero-cost days are still visible as a sliver.
                const ratio = maxCost > 0 ? d.cost_usd / maxCost : 0;
                const heightPct = Math.max(4, ratio * 100);
                return (
                  <Box
                    key={d.date}
                    data-testid="ai-spend-bar"
                    title={`${d.date}: $${d.cost_usd.toFixed(4)}`}
                    sx={{
                      flex: 1,
                      height: `${heightPct}%`,
                      minWidth: 4,
                      borderRadius: 0.5,
                      bgcolor: "primary.main",
                    }}
                  />
                );
              })}
            </Stack>
          )}
        </Box>
      </CardContent>
    </Card>
  );
}
