"""Storage backend abstraction for owned media assets.

Design:
- ``StorageBackend`` is a ``@runtime_checkable`` Protocol; any class that
  implements the four methods is a valid backend without explicit inheritance.
- ``FakeStorageBackend`` — in-memory; used by tests (no cloud credentials).
  ``put(key, size)`` simulates a client PUT; ``head`` / presign / delete work
  against the in-memory dict.
- ``S3StorageBackend`` — boto3 client targeting R2 (or any S3-compatible store)
  via ``endpoint_url``. Presigning is a *local* operation (no network call).
  ``head`` uses ``head_object``; returns None on 404/NoSuchKey ClientError.
- ``get_storage()`` — FastAPI dependency.  Returns ``S3StorageBackend`` when
  ``settings.STORAGE_BUCKET`` is set; raises ``HTTPException(503)`` otherwise,
  mirroring the SMTP-disabled pattern (Global Constraint).

  Tests override it via ``app.dependency_overrides[get_storage]``.

Content-address simplification (v1): the confirm endpoint trusts the client's
sha256; the server only verifies *size* via HEAD (catches truncation). Full
server-side sha256 would require downloading the object — deferred. Authors
are trusted, tenant-scoped.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import boto3
import botocore.exceptions
from fastapi import HTTPException

from app.config import settings


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class StorageBackend(Protocol):
    """Minimal object-storage interface consumed by the media router."""

    def presigned_put_url(self, key: str, content_type: str) -> str:
        """Return a presigned URL the client can use to PUT the object directly.

        No network call — presigning is computed locally from the credentials.
        """
        ...

    def presigned_get_url(self, key: str) -> str:
        """Return a presigned URL that allows reading the object."""
        ...

    def put_bytes(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> None:
        """Upload raw bytes to ``key`` from the server side.

        Unlike client uploads (which go direct via ``presigned_put_url``), some
        artifacts are generated server-side — e.g. rendered slide decks — and
        the server must write them itself. Overwrites any existing object.
        """
        ...

    def head(self, key: str) -> dict | None:
        """Return object metadata or None if the object does not exist.

        Returns ``{"size": <int bytes>}`` on success, ``None`` on 404.
        """
        ...

    def fetch(self, key: str) -> bytes:
        """Return the object's raw bytes.

        Raises ``FileNotFoundError`` if the object does not exist. Used by the
        transcription pipeline to read AV/PDF/doc bytes server-side (the only
        flow that proxies bytes; uploads/downloads still go direct via presign).
        """
        ...

    def delete(self, key: str) -> None:
        """Delete the object.  Must not raise if the object is absent."""
        ...


# ---------------------------------------------------------------------------
# FakeStorageBackend (tests only)
# ---------------------------------------------------------------------------


class FakeStorageBackend:
    """In-memory storage backend for tests.

    ``presigned_*`` return deterministic fake URLs so tests can assert the
    key is embedded.  ``put(key, size)`` simulates a client upload and makes
    ``head`` reflect it.
    """

    def __init__(self) -> None:
        # Internal store: key → size (bytes)
        self._objects: dict[str, int] = {}
        # Optional byte store: key → raw bytes (only when put_bytes is used).
        self._data: dict[str, bytes] = {}

    # Test-only helper — not part of the StorageBackend Protocol.
    def put(self, key: str, size: int) -> None:
        """Simulate a client PUT by registering (key, size) in the in-memory store."""
        self._objects[key] = size

    # Server-side upload. Satisfies the StorageBackend Protocol (real bytes so
    # ``fetch`` returns them and ``head`` reflects their length). ``content_type``
    # is accepted for Protocol parity; the in-memory Fake does not track it.
    def put_bytes(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"  # noqa: ARG002
    ) -> None:
        """Simulate a server-side PUT of real bytes (drives ``fetch`` + ``head``)."""
        self._objects[key] = len(data)
        self._data[key] = data

    # --- Protocol implementation -------------------------------------------

    def presigned_put_url(self, key: str, content_type: str) -> str:  # noqa: ARG002
        return f"https://fake-storage/put/{key}"

    def presigned_get_url(self, key: str) -> str:
        return f"https://fake-storage/get/{key}"

    def head(self, key: str) -> dict | None:
        size = self._objects.get(key)
        if size is None:
            return None
        return {"size": size}

    def fetch(self, key: str) -> bytes:
        data = self._data.get(key)
        if data is None:
            raise FileNotFoundError(key)
        return data

    def delete(self, key: str) -> None:
        self._objects.pop(key, None)
        self._data.pop(key, None)


# ---------------------------------------------------------------------------
# S3StorageBackend (production — R2 / S3-compatible)
# ---------------------------------------------------------------------------


class S3StorageBackend:
    """boto3-backed storage targeting R2 (or any S3-compatible endpoint).

    ``endpoint_url`` must be set for R2; leave empty for native AWS S3.
    Presigning is a local operation — no outbound network call is made until
    the *client* uses the returned URL to PUT/GET the object.
    """

    def __init__(
        self,
        endpoint_url: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        region: str = "auto",
        presign_expiry_s: int = 3600,
    ) -> None:
        self._bucket = bucket
        self._expiry = presign_expiry_s
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
        )

    def presigned_put_url(self, key: str, content_type: str) -> str:
        return self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self._bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=self._expiry,
        )

    def presigned_get_url(self, key: str) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=self._expiry,
        )

    def put_bytes(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> None:
        """Server-side upload via ``put_object`` (no presign — the server holds
        the bytes). Used for generated artifacts such as rendered slide decks."""
        self._client.put_object(
            Bucket=self._bucket, Key=key, Body=data, ContentType=content_type
        )

    def head(self, key: str) -> dict | None:
        try:
            resp = self._client.head_object(Bucket=self._bucket, Key=key)
            return {"size": resp["ContentLength"]}
        except botocore.exceptions.ClientError:
            return None

    def fetch(self, key: str) -> bytes:
        try:
            resp = self._client.get_object(Bucket=self._bucket, Key=key)
        except botocore.exceptions.ClientError as exc:  # 404 / NoSuchKey
            raise FileNotFoundError(key) from exc
        return resp["Body"].read()

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except botocore.exceptions.ClientError:
            # Best-effort — log caller is responsible; the router logs on failure.
            pass


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


def get_storage() -> StorageBackend:
    """FastAPI dependency that returns the configured StorageBackend.

    Raises ``HTTPException(503)`` when ``STORAGE_BUCKET`` is empty, mirroring
    the SMTP-disabled pattern.  Tests inject the Fake via::

        app.dependency_overrides[get_storage] = lambda: FakeStorageBackend()
    """
    if not settings.STORAGE_BUCKET:
        raise HTTPException(
            status_code=503,
            detail=(
                "Media storage is not configured. "
                "Set STORAGE_BUCKET (and related STORAGE_* vars) to enable."
            ),
        )
    return S3StorageBackend(
        endpoint_url=settings.STORAGE_ENDPOINT_URL,
        bucket=settings.STORAGE_BUCKET,
        access_key_id=settings.STORAGE_ACCESS_KEY_ID,
        secret_access_key=settings.STORAGE_SECRET_ACCESS_KEY,
        region=settings.STORAGE_REGION,
        presign_expiry_s=settings.STORAGE_PRESIGN_EXPIRY_S,
    )
