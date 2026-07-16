import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// Mock the shared axios client so we can assert the two apiClient calls
// (upload-url + confirm) without a network. The raw PUT to storage uses global
// fetch, which we stub separately — proving apiClient never touches the R2 URL.
vi.mock("../api/client", () => ({
  apiClient: { post: vi.fn(), get: vi.fn() },
}));

import { apiClient } from "../api/client";
import { uploadMedia, kindFromMime } from "../api/media";

const mockedPost = vi.mocked(apiClient.post);

describe("kindFromMime", () => {
  it("maps MIME types to the backend MediaKind enum", () => {
    expect(kindFromMime("image/png")).toBe("image");
    expect(kindFromMime("video/mp4")).toBe("video");
    expect(kindFromMime("audio/mpeg")).toBe("audio");
    expect(kindFromMime("application/pdf")).toBe("pdf");
    expect(kindFromMime("application/msword")).toBe("doc");
    expect(kindFromMime("")).toBe("doc");
  });
});

describe("uploadMedia — 3-step presigned flow", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();

    // Deterministic sha256: return a 4-byte digest → "01020304" hex.
    vi.stubGlobal("crypto", {
      subtle: {
        digest: vi.fn().mockResolvedValue(new Uint8Array([1, 2, 3, 4]).buffer),
      },
    });

    fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    vi.stubGlobal("fetch", fetchMock);

    mockedPost.mockImplementation((url: string) => {
      if (url === "/media/upload-url") {
        return Promise.resolve({
          data: {
            asset_id: "asset-123",
            upload_url: "https://r2.example.com/presigned?sig=abc",
            storage_key: "org/media/x/clip.mp4",
          },
        });
      }
      // confirm
      return Promise.resolve({
        data: {
          id: "asset-123",
          kind: "video",
          filename: "clip.mp4",
          mime: "video/mp4",
          size_bytes: 5,
          checksum: "01020304",
          duration_s: null,
          status: "ready",
          storage_key: "org/media/x/clip.mp4",
          created_at: "2026-01-01T00:00:00Z",
        },
      });
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("PUTs with Content-Type EXACTLY equal to file.type and confirms with the sha256 hex", async () => {
    const file = new File(["bytes"], "clip.mp4", { type: "video/mp4" });
    const asset = await uploadMedia(file);

    // Step 1: upload-url with derived kind.
    expect(mockedPost).toHaveBeenCalledWith("/media/upload-url", {
      filename: "clip.mp4",
      mime: "video/mp4",
      kind: "video",
    });

    // Step 2: raw fetch PUT to the presigned URL (NOT apiClient), Content-Type
    // header EXACTLY the registered mime — R2 binds it into the signature.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://r2.example.com/presigned?sig=abc");
    expect(init.method).toBe("PUT");
    expect(init.body).toBe(file);
    expect(init.headers).toEqual({ "Content-Type": "video/mp4" });

    // Step 3: confirm with the hex checksum derived from crypto.subtle.digest.
    expect(mockedPost).toHaveBeenCalledWith("/media/asset-123/confirm", {
      checksum: "01020304",
    });
    expect(asset.status).toBe("ready");
  });

  it("throws (and never confirms) when the storage PUT is rejected (e.g. 403)", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 403, statusText: "Forbidden" });
    const file = new File(["bytes"], "clip.mp4", { type: "video/mp4" });

    await expect(uploadMedia(file)).rejects.toThrow(/403 Forbidden/);
    // upload-url was called, but confirm was NOT (only the one POST).
    expect(mockedPost).toHaveBeenCalledTimes(1);
    expect(mockedPost).toHaveBeenCalledWith("/media/upload-url", expect.anything());
  });
});
