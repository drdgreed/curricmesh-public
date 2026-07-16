"""Live R2/S3 smoke test for the media storage backend.

Exercises the REAL S3StorageBackend against a live bucket — the thing the unit
tests can only mock. Run this ONCE after provisioning R2 (or any S3-compatible
bucket) and setting the STORAGE_* env vars, before trusting media in prod.

It validates the failure mode the final review flagged: R2 binds the
Content-Type into the presigned-PUT signature, so the client PUT must send the
EXACT content-type passed to presigned_put_url — a mismatch 403s. This script
sends the matching header (the correct path) and a deliberately-mismatched one
(to prove the guard behaves), then round-trips head → GET → delete.

Usage (from backend/, venv active), with the six STORAGE_* vars exported:
    STORAGE_ENDPOINT_URL=https://<acct>.r2.cloudflarestorage.com \
    STORAGE_BUCKET=<bucket> STORAGE_ACCESS_KEY_ID=<id> \
    STORAGE_SECRET_ACCESS_KEY=<secret> STORAGE_REGION=auto \
    python -m scripts.smoke_media_r2

Exit code 0 = all steps passed. Non-zero = a step failed (details printed).
No app server or DB needed — this tests the storage adapter directly.
"""
from __future__ import annotations

import sys
import uuid

import httpx

from app.config import settings
from app.media.storage import get_storage


def _fail(msg: str) -> None:
    print(f"  ✗ {msg}")
    sys.exit(1)


def main() -> None:
    if not settings.STORAGE_BUCKET:
        _fail("STORAGE_BUCKET is empty — set the STORAGE_* env vars first (see docs/MEDIA_SUBSYSTEM.md).")

    print(f"R2/S3 smoke test → bucket={settings.STORAGE_BUCKET} endpoint={settings.STORAGE_ENDPOINT_URL or '(native S3)'}")
    storage = get_storage()  # same construction path the app uses (reads STORAGE_* settings)
    key = f"_smoke/{uuid.uuid4()}/hello.txt"
    body = b"curricmesh media smoke test\n"
    ctype = "text/plain"

    with httpx.Client(timeout=30) as http:
        # 1. presigned PUT with the MATCHING content-type (the correct path).
        put_url = storage.presigned_put_url(key, ctype)
        r = http.put(put_url, content=body, headers={"Content-Type": ctype})
        if r.status_code not in (200, 201, 204):
            _fail(f"PUT with matching Content-Type failed: HTTP {r.status_code} — check bucket perms + endpoint. Body: {r.text[:200]}")
        print("  ✓ presigned PUT (matching Content-Type) succeeded")

        # 2. HEAD reflects the object + correct size.
        head = storage.head(key)
        if head is None:
            _fail("head() returned None right after a successful PUT — head_object/ClientError handling or eventual-consistency issue.")
        if head.get("size") != len(body):
            _fail(f"head() size mismatch: got {head.get('size')}, expected {len(body)}")
        print(f"  ✓ head() reports size={head['size']}")

        # 3. presigned GET returns the exact bytes.
        get_url = storage.presigned_get_url(key)
        r = http.get(get_url)
        if r.status_code != 200 or r.content != body:
            _fail(f"GET round-trip mismatch: HTTP {r.status_code}, {len(r.content)} bytes")
        print("  ✓ presigned GET returned the exact bytes")

        # 4. Content-Type binding guard: a MISMATCHED header on a fresh presign
        #    must be rejected by R2/S3 (proves the signature binds content-type;
        #    this is the #1 first-setup foot-gun).
        key2 = f"_smoke/{uuid.uuid4()}/mismatch.txt"
        put_url2 = storage.presigned_put_url(key2, "text/plain")
        r = http.put(put_url2, content=body, headers={"Content-Type": "application/octet-stream"})
        if r.status_code in (200, 201, 204):
            print("  ⚠ mismatched Content-Type PUT was ACCEPTED — this backend/bucket does NOT bind content-type in the signature (some S3 configs don't). Note it; the app sends matching types so this is informational.")
            storage.delete(key2)
        else:
            print(f"  ✓ mismatched Content-Type PUT rejected (HTTP {r.status_code}) — signature binds content-type as expected")

    # 5. delete removes the object.
    storage.delete(key)
    if storage.head(key) is not None:
        _fail("delete() did not remove the object (head() still returns it)")
    print("  ✓ delete() removed the object")

    print("\nSMOKE TEST PASSED — R2/S3 media backend is live-verified.")


if __name__ == "__main__":
    main()
