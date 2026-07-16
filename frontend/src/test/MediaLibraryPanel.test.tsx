import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Mock the media API. The panel's list + upload flow are driven through these.
vi.mock("../api/media", () => ({
  listMedia: vi.fn(),
  uploadMedia: vi.fn(),
}));

import { MediaLibraryPanel } from "../pages/builder/MediaLibraryPanel";
import type { MediaAsset } from "../api/media";
import { listMedia, uploadMedia } from "../api/media";

function makeAsset(over: Partial<MediaAsset>): MediaAsset {
  return {
    id: "a1",
    kind: "video",
    filename: "intro.mp4",
    mime: "video/mp4",
    size_bytes: 1024,
    checksum: "abc",
    duration_s: null,
    status: "ready",
    storage_key: "org/media/x/intro.mp4",
    created_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

function renderPanel() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MediaLibraryPanel />
    </QueryClientProvider>
  );
}

function selectFile(name = "intro.mp4", type = "video/mp4") {
  const input = screen.getByTestId("media-upload-input") as HTMLInputElement;
  const file = new File(["bytes"], name, { type });
  fireEvent.change(input, { target: { files: [file] } });
  return file;
}

describe("MediaLibraryPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listMedia).mockResolvedValue([]);
    vi.mocked(uploadMedia).mockResolvedValue(makeAsset({}));
  });

  it("renders the upload control AND an empty-state when the library is empty", async () => {
    renderPanel();
    // The whole bug: the upload control must show even with zero assets.
    expect(await screen.findByTestId("media-upload-btn")).toBeInTheDocument();
    expect(screen.getByTestId("media-upload-input")).toBeInTheDocument();
    expect(await screen.findByTestId("media-library-empty")).toBeInTheDocument();
  });

  it("lists existing assets with their status", async () => {
    vi.mocked(listMedia).mockResolvedValue([
      makeAsset({ id: "a1", filename: "intro.mp4", status: "ready" }),
      makeAsset({ id: "a2", filename: "wip.pdf", kind: "pdf", status: "pending" }),
    ]);
    renderPanel();
    await waitFor(() =>
      expect(screen.getAllByTestId("media-library-item")).toHaveLength(2)
    );
    expect(screen.getByText("intro.mp4")).toBeInTheDocument();
    expect(screen.getByText("wip.pdf")).toBeInTheDocument();
    expect(screen.getByText("ready")).toBeInTheDocument();
    expect(screen.getByText("pending")).toBeInTheDocument();
  });

  it("selecting a file calls uploadMedia and refreshes the list", async () => {
    // First list load empty; after upload it returns the new asset.
    vi.mocked(listMedia)
      .mockResolvedValueOnce([])
      .mockResolvedValue([makeAsset({ id: "a1", filename: "intro.mp4" })]);

    renderPanel();
    await screen.findByTestId("media-library-empty");

    const file = selectFile();

    await waitFor(() => expect(uploadMedia).toHaveBeenCalledWith(file));
    // The ["media-list"] invalidation triggers a refetch that now yields 1 item.
    await waitFor(() =>
      expect(screen.getAllByTestId("media-library-item")).toHaveLength(1)
    );
  });

  it("surfaces a 503 as a clear storage-not-configured notice", async () => {
    vi.mocked(uploadMedia).mockRejectedValue({ response: { status: 503 } });

    renderPanel();
    await screen.findByTestId("media-upload-btn");
    selectFile();

    expect(
      await screen.findByTestId("media-storage-not-configured")
    ).toBeInTheDocument();
    // A 503 is NOT rendered as a scary generic error.
    expect(screen.queryByTestId("media-upload-error")).not.toBeInTheDocument();
  });

  it("shows a generic error when the upload fails for a non-503 reason", async () => {
    vi.mocked(uploadMedia).mockRejectedValue(
      new Error("Upload to storage failed (403 Forbidden)")
    );

    renderPanel();
    await screen.findByTestId("media-upload-btn");
    selectFile();

    const err = await screen.findByTestId("media-upload-error");
    expect(err).toHaveTextContent("403 Forbidden");
  });
});
