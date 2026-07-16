/**
 * Owned-media API client (Authoring slice 1/2). Mirrors the backend contract
 * under ``/api/v1/media`` (app/routers/media.py). Reuses the shared axios
 * `apiClient` whose baseURL is `.../api/v1`, so paths here are prefixed
 * with `/media`.
 *
 * verbatimModuleSyntax is on → the type surface is exported with `export type`
 * and consumers must `import type { … }`.
 */

import { apiClient } from "./client";

export type MediaKind = "video" | "audio" | "image" | "pdf" | "doc" | "other";
export type MediaStatus = "pending" | "ready" | "failed";

export interface MediaAsset {
  id: string;
  kind: string;
  filename: string;
  mime: string;
  size_bytes: number | null;
  checksum: string | null;
  duration_s: number | null;
  status: string;
  storage_key: string;
  created_at: string;
}

/** List the org's media assets, optionally filtered by status / kind. */
export async function listMedia(params?: {
  status?: MediaStatus;
  kind?: MediaKind;
}): Promise<MediaAsset[]> {
  const { data } = await apiClient.get<MediaAsset[]>("/media", { params });
  return data;
}

/** The org's ready-to-embed assets (upload confirmed). */
export async function listReadyMedia(): Promise<MediaAsset[]> {
  return listMedia({ status: "ready" });
}

// ---------------------------------------------------------------------------
// Upload (slice 3) — presigned direct-to-storage, 3-step flow
// ---------------------------------------------------------------------------

interface UploadUrlResponse {
  asset_id: string;
  upload_url: string;
  storage_key: string;
}

/**
 * Map a File's MIME type to the backend ``MediaKind`` enum. Anything we can't
 * classify (empty type, unknown application/*, etc.) falls back to ``doc`` —
 * the backend accepts it as generic and it stays attachable.
 */
export function kindFromMime(mime: string): MediaKind {
  if (mime.startsWith("image/")) return "image";
  if (mime.startsWith("video/")) return "video";
  if (mime.startsWith("audio/")) return "audio";
  if (mime === "application/pdf") return "pdf";
  return "doc";
}

/** Compute the file's SHA-256 as a lowercase hex string (browser-native). */
async function sha256Hex(file: File): Promise<string> {
  const buffer = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/**
 * Upload a file into the org's media library via the presigned direct-to-storage
 * flow, returning the confirmed (``ready``) asset.
 *
 * Three steps, exactly matching app/routers/media.py:
 *   1. POST /media/upload-url  → { asset_id, upload_url, storage_key }
 *   2. PUT the raw bytes to ``upload_url`` with **Content-Type === file.type**.
 *      R2 binds the Content-Type into the presigned signature, so a mismatch is
 *      a 403 — we use raw `fetch` (NOT apiClient) so no auth interceptor rewrites
 *      the cross-origin PUT, and we set the header explicitly to the same MIME we
 *      registered in step 1.
 *   3. POST /media/{asset_id}/confirm  → the ready MediaAsset.
 */
export async function uploadMedia(file: File): Promise<MediaAsset> {
  const mime = file.type || "application/octet-stream";
  const kind = kindFromMime(mime);

  // Compute the checksum before we register, so a hashing failure never leaves
  // a dangling pending asset.
  const checksum = await sha256Hex(file);

  const { data: presign } = await apiClient.post<UploadUrlResponse>(
    "/media/upload-url",
    { filename: file.name, mime, kind }
  );

  // Raw fetch — apiClient's baseURL + auth interceptor must NOT touch the
  // presigned R2 URL. Content-Type MUST equal the `mime` sent above or R2
  // rejects the signature with 403.
  const putRes = await fetch(presign.upload_url, {
    method: "PUT",
    body: file,
    headers: { "Content-Type": mime },
  });
  if (!putRes.ok) {
    throw new Error(
      `Upload to storage failed (${putRes.status} ${putRes.statusText})`
    );
  }

  const { data: asset } = await apiClient.post<MediaAsset>(
    `/media/${presign.asset_id}/confirm`,
    { checksum }
  );
  return asset;
}
