import { useQuery } from "@tanstack/react-query";
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
import { Link as RouterLink } from "react-router-dom";
import { getAlignment, getDashboard } from "../api/client";
import type {
  AlignmentItem,
  CurriculumSummary,
  VersionSummary,
} from "../api/client";
import Accordion from "@mui/material/Accordion";
import AccordionSummary from "@mui/material/AccordionSummary";
import AccordionDetails from "@mui/material/AccordionDetails";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import { StatusBadge } from "../components/StatusBadge";
import { PageHeader } from "../components/PageHeader";
import { AiSpendTile } from "../components/AiSpendTile";
import { useAuth } from "../auth/AuthContext";

const AUTHOR_ROLES = new Set(["architect", "program_manager"]);

function VersionRow({ v, isCurrent }: { v: VersionSummary; isCurrent: boolean }) {
  return (
    <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
      <Typography variant="body2" sx={{ fontFamily: "monospace", minWidth: 80 }}>
        v{v.semver}
      </Typography>
      <StatusBadge status={v.status} />
      {isCurrent && (
        <Chip label="current" size="small" color="info" variant="outlined" />
      )}
    </Stack>
  );
}

function CurriculumCard({ curriculum }: { curriculum: CurriculumSummary }) {
  // Precise per-curriculum alignment (mode + revision_delta). Falls back to the
  // dashboard's legacy timestamp-only entries only as a count hint while loading.
  const { data: alignment } = useQuery({
    queryKey: ["alignment", curriculum.id],
    queryFn: () => getAlignment(curriculum.id),
  });
  const alignmentItems = alignment?.items ?? [];

  const activeCohortsCount = curriculum.cohorts.filter((c) => {
    if (!c.start_date) return false;
    const today = new Date().toISOString().slice(0, 10); // "YYYY-MM-DD"
    const started = c.start_date <= today;
    const notEnded = c.end_date == null || c.end_date >= today;
    return started && notEnded;
  }).length;

  return (
    <Card variant="outlined">
      <CardContent>
        <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "flex-start" }}>
          <Box>
            <Typography variant="h6">{curriculum.name}</Typography>
            <Typography variant="caption" color="text.secondary">
              {curriculum.slug}
            </Typography>
          </Box>
          <Typography variant="body2" color="text.secondary">
            {activeCohortsCount} active cohort{activeCohortsCount !== 1 ? "s" : ""}
          </Typography>
        </Stack>

        <Divider sx={{ my: 1.5 }} />

        <Typography variant="subtitle2" gutterBottom>
          Versions
        </Typography>
        <Stack spacing={0.75}>
          {curriculum.versions.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No versions yet
            </Typography>
          ) : (
            curriculum.versions.map((v) => (
              <VersionRow
                key={v.id}
                v={v}
                isCurrent={v.id === curriculum.current_version_id}
              />
            ))
          )}
        </Stack>

        {alignmentItems.length > 0 && (
          <AlignmentWarning items={alignmentItems} />
        )}
      </CardContent>
    </Card>
  );
}

/**
 * The staleness summary for one alignment item — a warning chip ("N revisions
 * behind" for revision-mode, "needs review" for the legacy timestamp heuristic).
 */
function StalenessChip({ item }: { item: AlignmentItem }) {
  const label =
    item.mode === "revision" && item.revision_delta != null
      ? `${item.revision_delta} revision${item.revision_delta === 1 ? "" : "s"} behind`
      : "needs review";
  return (
    <Chip
      label={label}
      size="small"
      color="warning"
      variant="outlined"
      sx={{ height: 22, fontSize: 11, fontWeight: 600 }}
    />
  );
}

function AlignmentWarning({ items }: { items: AlignmentItem[] }) {
  return (
    <Accordion
      disableGutters
      variant="outlined"
      defaultExpanded={false}
      data-testid="alignment-accordion"
      sx={{
        mt: 1.5,
        bgcolor: "warning.50",
        borderColor: "warning.light",
      }}
    >
      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
        <Typography variant="body2" sx={{ fontWeight: 600, color: "warning.dark" }}>
          ⚠ {items.length} out-of-alignment asset
          {items.length !== 1 ? "s" : ""}
        </Typography>
      </AccordionSummary>
      <AccordionDetails sx={{ pt: 0 }}>
        <List dense disablePadding>
          {items.map((item, i) => (
            <ListItem
              key={`${item.dependent_id}-${item.prerequisite_id}-${i}`}
              data-testid="alignment-row"
              disableGutters
              sx={{
                display: "flex",
                flexWrap: "wrap",
                alignItems: "center",
                gap: 1,
                py: 0.5,
              }}
            >
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                {item.dependent_label}
              </Typography>
              <StalenessChip item={item} />
              <Typography variant="body2" color="text.secondary">
                vs {item.prerequisite_label}
              </Typography>
            </ListItem>
          ))}
        </List>
      </AccordionDetails>
    </Accordion>
  );
}

export function Dashboard() {
  const { role } = useAuth();
  const canAuthorCCR = role != null && AUTHOR_ROLES.has(role);
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
        Failed to load dashboard. Please try again.
      </Alert>
    );
  }

  return (
    <Box>
      <PageHeader
        title="Dashboard"
        subtitle="Curricula"
        actions={
          canAuthorCCR && (
            <Button
              variant="contained"
              component={RouterLink}
              to="/ccrs/new"
            >
              New Change Request
            </Button>
          )
        }
      />

      {canAuthorCCR && (
        <Box sx={{ mb: 2 }}>
          <AiSpendTile />
        </Box>
      )}

      {data.curricula.length === 0 ? (
        <Typography color="text.secondary">No curricula found.</Typography>
      ) : (
        <Stack spacing={2}>
          {data.curricula.map((c) => (
            <CurriculumCard key={c.id} curriculum={c} />
          ))}
        </Stack>
      )}
    </Box>
  );
}
