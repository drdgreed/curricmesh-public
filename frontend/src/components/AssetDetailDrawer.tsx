/**
 * AssetDetailDrawer — a right-side drawer that opens when a course tile is
 * clicked. Shows the asset's selected content, an editable source link, its
 * immutable revision history (the ContentVersion seq chain), and its
 * prerequisites / dependents (clickable to walk the graph). Reads
 * GET /assets/{id}; editors can PATCH /assets/{id}/source-url.
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Divider from "@mui/material/Divider";
import Drawer from "@mui/material/Drawer";
import IconButton from "@mui/material/IconButton";
import Link from "@mui/material/Link";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import CloseIcon from "@mui/icons-material/Close";
import LaunchIcon from "@mui/icons-material/Launch";

import type { AssetEdgeRef, AssetKind } from "../api/client";
import {
  ASSET_KIND_LABELS,
  getAlignment,
  getAssetDetail,
  setAssetSourceUrl,
} from "../api/client";
import { AssetContent } from "./AssetContent";

const EDIT_ROLES = new Set([
  "architect",
  "instructor",
  "instructor_lead",
  "devops",
]);

function kindLabel(kind: string): string {
  return ASSET_KIND_LABELS[kind as AssetKind] ?? kind;
}

/**
 * Return the URL only if it uses a safe http(s) scheme, else null. Guards
 * against stored-XSS via `javascript:`/`data:` URLs in the user-supplied
 * source_url (it is rendered as an <a href> and editable by authors).
 */
function safeHttpUrl(raw: string | null | undefined): string | null {
  if (!raw) return null;
  try {
    const u = new URL(raw);
    return u.protocol === "https:" || u.protocol === "http:" ? u.toString() : null;
  } catch {
    return null;
  }
}

interface Props {
  assetId: string | null;
  curriculumId?: string;
  role: string | null;
  onClose: () => void;
  onNavigate: (assetId: string) => void;
}

export function AssetDetailDrawer({
  assetId,
  curriculumId,
  role,
  onClose,
  onNavigate,
}: Props) {
  const queryClient = useQueryClient();
  const open = assetId !== null;

  const { data, isLoading, isError } = useQuery({
    queryKey: ["asset-detail", assetId],
    queryFn: () => getAssetDetail(assetId as string),
    enabled: open,
  });

  // Curriculum alignment (cached, shared with the Dashboard query key) so we can
  // flag the open asset inline when it's a stale dependent.
  const { data: alignment } = useQuery({
    queryKey: ["alignment", curriculumId],
    queryFn: () => getAlignment(curriculumId as string),
    enabled: open && !!curriculumId,
  });
  const staleAgainst = (alignment?.items ?? []).filter(
    (it) => it.dependent_id === assetId
  );

  const [urlDraft, setUrlDraft] = useState("");
  const [urlError, setUrlError] = useState<string | null>(null);
  useEffect(() => {
    setUrlDraft(data?.source_url ?? "");
    setUrlError(null);
  }, [data?.source_url, assetId]);

  const canEdit = role !== null && EDIT_ROLES.has(role);

  const saveUrl = useMutation({
    mutationFn: () =>
      setAssetSourceUrl(assetId as string, urlDraft.trim() || null),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["asset-detail", assetId] });
      queryClient.invalidateQueries({ queryKey: ["course-calendar"] });
    },
  });

  function handleSaveUrl() {
    const trimmed = urlDraft.trim();
    // Allow clearing (empty), but any non-empty value must be a safe http(s) URL.
    if (trimmed && !safeHttpUrl(trimmed)) {
      setUrlError("Enter a valid http:// or https:// URL.");
      return;
    }
    setUrlError(null);
    saveUrl.mutate();
  }

  const edgeList = (edges: AssetEdgeRef[], empty: string) =>
    edges.length === 0 ? (
      <Typography variant="body2" color="text.secondary">
        {empty}
      </Typography>
    ) : (
      <Stack spacing={0.75}>
        {edges.map((e) => (
          <Box
            key={e.id}
            onClick={() => onNavigate(e.id)}
            sx={{
              cursor: "pointer",
              px: 1.25,
              py: 0.75,
              borderRadius: 1.5,
              border: "1px solid",
              borderColor: "divider",
              "&:hover": { borderColor: "primary.main", bgcolor: "action.hover" },
            }}
          >
            <Typography variant="body2" sx={{ fontWeight: 500 }}>
              {e.label}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {e.lineage_key}
            </Typography>
          </Box>
        ))}
      </Stack>
    );

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      slotProps={{
        paper: {
          sx: { width: { xs: "100%", sm: 460 }, p: 0 },
        },
      }}
    >
      {/* Header */}
      <Box
        data-testid="asset-drawer"
        sx={{
          px: 2.5,
          py: 2,
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Box sx={{ pr: 1 }}>
          {data && (
            <Chip
              label={kindLabel(data.kind)}
              size="small"
              color="secondary"
              variant="outlined"
              sx={{ mb: 0.75 }}
            />
          )}
          <Typography variant="h6" sx={{ lineHeight: 1.25 }}>
            {data?.label ?? "Loading…"}
          </Typography>
          {data && (
            <Typography variant="caption" color="text.secondary">
              {data.lineage_key}
              {data.content_seq != null ? ` · rev ${data.content_seq}` : ""}
            </Typography>
          )}
        </Box>
        <IconButton onClick={onClose} size="small" aria-label="close">
          <CloseIcon fontSize="small" />
        </IconButton>
      </Box>

      <Box sx={{ p: 2.5, overflowY: "auto" }}>
        {isLoading && (
          <Box sx={{ display: "flex", justifyContent: "center", mt: 4 }}>
            <CircularProgress />
          </Box>
        )}
        {isError && <Alert severity="error">Failed to load this asset.</Alert>}

        {data && (
          <Stack spacing={2.5}>
            {/* Source link */}
            <Box>
              <Typography variant="overline" color="text.secondary">
                Source content
              </Typography>
              {safeHttpUrl(data.source_url) ? (
                <Link
                  href={safeHttpUrl(data.source_url) as string}
                  target="_blank"
                  rel="noopener noreferrer"
                  sx={{ display: "inline-flex", alignItems: "center", gap: 0.5 }}
                >
                  Open source <LaunchIcon sx={{ fontSize: 15 }} />
                </Link>
              ) : data.source_url ? (
                <Typography variant="body2" color="warning.main">
                  Source link uses an unsupported scheme and was not rendered.
                </Typography>
              ) : (
                <Typography variant="body2" color="text.secondary">
                  No source link set.
                </Typography>
              )}
              {canEdit && (
                <Box sx={{ mt: 1, display: "flex", gap: 1 }}>
                  <TextField
                    size="small"
                    fullWidth
                    placeholder="https://…"
                    value={urlDraft}
                    error={urlError !== null}
                    helperText={urlError ?? undefined}
                    onChange={(e) => {
                      setUrlDraft(e.target.value);
                      if (urlError) setUrlError(null);
                    }}
                  />
                  <Button
                    variant="outlined"
                    size="small"
                    sx={{ alignSelf: "flex-start" }}
                    disabled={
                      saveUrl.isPending ||
                      urlDraft.trim() === (data.source_url ?? "")
                    }
                    onClick={handleSaveUrl}
                  >
                    Save
                  </Button>
                </Box>
              )}
              {saveUrl.isError && (
                <Alert severity="error" sx={{ mt: 1 }}>
                  Could not save the source link.
                </Alert>
              )}
            </Box>

            <Divider />

            {/* Content body */}
            <Box>
              <Typography variant="overline" color="text.secondary">
                Content
              </Typography>
              <Box
                sx={{
                  mt: 0.5,
                  p: 1.5,
                  borderRadius: 1.5,
                  bgcolor: "background.default",
                  border: "1px solid",
                  borderColor: "divider",
                  maxHeight: 320,
                  overflowY: "auto",
                }}
              >
                <AssetContent kind={data.kind} content={data.content} />
              </Box>
            </Box>

            {/* Prerequisites / dependents */}
            <Box>
              <Typography variant="overline" color="text.secondary">
                Prerequisites
              </Typography>
              {staleAgainst.length > 0 && (
                <Stack
                  spacing={0.5}
                  data-testid="asset-staleness-note"
                  sx={{ mt: 0.5, mb: 1 }}
                >
                  {staleAgainst.map((it, i) => (
                    <Typography
                      key={`${it.prerequisite_id}-${i}`}
                      variant="body2"
                      sx={{ color: "warning.main", fontWeight: 600 }}
                    >
                      ⚠{" "}
                      {it.mode === "revision" && it.revision_delta != null
                        ? `${it.revision_delta} revision${
                            it.revision_delta === 1 ? "" : "s"
                          } behind ${it.prerequisite_label}`
                        : `needs review against ${it.prerequisite_label}`}
                    </Typography>
                  ))}
                </Stack>
              )}
              <Box sx={{ mt: 0.5 }}>
                {edgeList(data.prerequisites, "No prerequisites.")}
              </Box>
            </Box>
            <Box>
              <Typography variant="overline" color="text.secondary">
                Dependents
              </Typography>
              <Box sx={{ mt: 0.5 }}>
                {edgeList(data.dependents, "Nothing depends on this yet.")}
              </Box>
            </Box>

            <Divider />

            {/* Revision history */}
            <Box>
              <Typography variant="overline" color="text.secondary">
                Revision history ({data.version_history.length})
              </Typography>
              <Stack spacing={0.5} sx={{ mt: 0.5 }}>
                {data.version_history.map((v) => (
                  <Box
                    key={v.seq}
                    sx={{ display: "flex", alignItems: "center", gap: 1 }}
                  >
                    <Chip
                      label={`rev ${v.seq}`}
                      size="small"
                      variant="outlined"
                      sx={{ height: 20, fontSize: 11 }}
                    />
                    <Typography
                      variant="caption"
                      sx={{ fontFamily: "monospace" }}
                    >
                      {v.content_hash.slice(0, 10)}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {new Date(v.created_at).toLocaleDateString()}
                    </Typography>
                  </Box>
                ))}
              </Stack>
            </Box>
          </Stack>
        )}
      </Box>
    </Drawer>
  );
}
