/**
 * MediaLibraryPanel — the missing "own the media" upload surface (slice 3).
 *
 * Slice 1 built the backend upload API and slice 2 built the attach-existing
 * picker (`AttachedMedia` in ObjectiveCanvas), but nothing ever let an author
 * get media INTO the library — so `listReadyMedia()` was always empty and the
 * attach picker (gated on `available.length > 0`) never rendered.
 *
 * This panel closes the loop: an upload control (always visible, even when the
 * library is empty) runs the 3-step presigned flow via `uploadMedia`, then
 * invalidates both this panel's list query AND the `["media-ready"]` query the
 * per-item attach picker reads — so a new asset appears immediately in both the
 * library and every item's "Attach media" dropdown.
 *
 * Graceful degradation: when storage is unconfigured the backend 503s on
 * upload-url; we surface that as a clear "storage not configured" notice rather
 * than a scary error, and the panel stays usable.
 */

import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Divider from "@mui/material/Divider";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import type { MediaAsset, MediaStatus } from "../../api/media";
import { listMedia, uploadMedia } from "../../api/media";

const STATUS_COLOR: Record<MediaStatus, "success" | "warning" | "error"> = {
  ready: "success",
  pending: "warning",
  failed: "error",
};

function statusColor(status: string): "success" | "warning" | "error" | "default" {
  return STATUS_COLOR[status as MediaStatus] ?? "default";
}

/** True when an error is the backend's "storage not configured" signal (503). */
function isStorageNotConfigured(err: unknown): boolean {
  return (err as { response?: { status?: number } })?.response?.status === 503;
}

export function MediaLibraryPanel() {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);

  // This panel's own list — ALL statuses, so a just-uploaded (or pending) asset
  // shows up here even before it is "ready".
  const listQuery = useQuery({
    queryKey: ["media-list"],
    queryFn: () => listMedia(),
  });

  const [uploadError, setUploadError] = useState<string | null>(null);

  const upload = useMutation({
    mutationFn: (file: File) => uploadMedia(file),
    onMutate: () => setUploadError(null),
    onSuccess: () => {
      // Refresh BOTH the library list and the ready-asset query the per-item
      // attach picker (AttachedMedia / insert-embed) reads, so the new asset is
      // immediately selectable without a reload.
      queryClient.invalidateQueries({ queryKey: ["media-list"] });
      queryClient.invalidateQueries({ queryKey: ["media-ready"] });
    },
    onError: (err) => {
      if (!isStorageNotConfigured(err)) {
        setUploadError(
          err instanceof Error ? err.message : "Upload failed. Please retry."
        );
      }
    },
  });

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    // Reset the input so selecting the same file again re-fires onChange.
    e.target.value = "";
    if (file) upload.mutate(file);
  }

  const assets: MediaAsset[] = listQuery.data ?? [];
  const storageNotConfigured =
    isStorageNotConfigured(upload.error) || isStorageNotConfigured(listQuery.error);

  return (
    <Paper variant="outlined" sx={{ p: 2.5 }} data-testid="media-library-panel">
      <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
        Media Library
      </Typography>
      <Typography variant="caption" color="text.secondary">
        Upload owned assets (video, audio, images, PDFs) to embed in course
        content.
      </Typography>

      {/* Upload control — ALWAYS rendered, never gated on having assets. */}
      <Box sx={{ mt: 1.5 }}>
        <input
          ref={fileInputRef}
          type="file"
          hidden
          onChange={handleFileChange}
          data-testid="media-upload-input"
        />
        <Button
          variant="outlined"
          fullWidth
          disabled={upload.isPending}
          onClick={() => fileInputRef.current?.click()}
          data-testid="media-upload-btn"
        >
          {upload.isPending ? "Uploading…" : "Upload media"}
        </Button>
      </Box>

      {upload.isPending && (
        <Stack
          direction="row"
          spacing={1}
          sx={{ mt: 1.5, alignItems: "center" }}
          data-testid="media-upload-progress"
        >
          <CircularProgress size={16} />
          <Typography variant="caption" color="text.secondary">
            Hashing &amp; uploading…
          </Typography>
        </Stack>
      )}

      {storageNotConfigured && (
        <Alert
          severity="info"
          sx={{ mt: 1.5 }}
          data-testid="media-storage-not-configured"
        >
          Media storage isn't configured on the server.
        </Alert>
      )}

      {uploadError && !storageNotConfigured && (
        <Alert severity="error" sx={{ mt: 1.5 }} data-testid="media-upload-error">
          {uploadError}
        </Alert>
      )}

      <Divider sx={{ my: 2 }} />

      {listQuery.isLoading ? (
        <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
          <CircularProgress size={20} />
        </Box>
      ) : assets.length === 0 ? (
        <Typography
          variant="body2"
          color="text.secondary"
          data-testid="media-library-empty"
        >
          No media yet. Upload a file to build your library — it'll then be
          attachable to any course item.
        </Typography>
      ) : (
        <Stack spacing={1} data-testid="media-library-list">
          {assets.map((a) => (
            <Box
              key={a.id}
              data-testid="media-library-item"
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 1,
                p: 1,
                borderRadius: 1.5,
                border: "1px solid",
                borderColor: "divider",
              }}
            >
              <Box sx={{ minWidth: 0, flexGrow: 1 }}>
                {/* Allow wrapping instead of ellipsis truncation: filenames
                    have no natural break points so overflowWrap:anywhere
                    breaks at character boundaries when the name is long.
                    title stays for accessibility. noWrap removed — it applied
                    text-overflow:ellipsis which the i18n detector flags. */}
                <Typography
                  variant="body2"
                  title={a.filename}
                  sx={{ overflowWrap: "anywhere" }}
                >
                  {a.filename}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {a.kind}
                </Typography>
              </Box>
              <Chip
                label={a.status}
                size="small"
                color={statusColor(a.status)}
                variant="outlined"
                sx={{ height: 20, fontSize: 11 }}
              />
            </Box>
          ))}
        </Stack>
      )}
    </Paper>
  );
}
