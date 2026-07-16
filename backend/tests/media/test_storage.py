"""Tests for app.media.storage — StorageBackend protocol + FakeStorageBackend.

TDD order: tests are written first; implementation follows. No cloud credentials
or network required — all tests run against FakeStorageBackend.

Structural coverage for S3StorageBackend is included via unittest.mock.patch so
the wiring (endpoint_url, presign method names, head_object ClientError path)
is verified without real network — no extra pytest plugins required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.media.storage import FakeStorageBackend, S3StorageBackend, StorageBackend


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_fake_satisfies_protocol():
    """FakeStorageBackend must be accepted as a StorageBackend at runtime."""
    assert isinstance(FakeStorageBackend(), StorageBackend)


# ---------------------------------------------------------------------------
# FakeStorageBackend — unit tests
# ---------------------------------------------------------------------------


class TestFakeStorageBackend:
    def test_put_and_head_round_trip(self):
        """put(key, size) then head(key) returns {"size": size}."""
        fake = FakeStorageBackend()
        fake.put("org/media/abc/file.mp4", 1024)
        result = fake.head("org/media/abc/file.mp4")
        assert result is not None
        assert result["size"] == 1024

    def test_head_absent_key_returns_none(self):
        """head() on an unknown key returns None (object not uploaded)."""
        fake = FakeStorageBackend()
        assert fake.head("does/not/exist") is None

    def test_presigned_put_url_contains_key(self):
        """presigned_put_url returns a URL that includes the storage key."""
        fake = FakeStorageBackend()
        key = "org/media/xyz/slide.pdf"
        url = fake.presigned_put_url(key, "application/pdf")
        assert key in url

    def test_presigned_get_url_contains_key(self):
        """presigned_get_url returns a URL that includes the storage key."""
        fake = FakeStorageBackend()
        key = "org/media/xyz/slide.pdf"
        url = fake.presigned_get_url(key)
        assert key in url

    def test_delete_removes_object(self):
        """delete() removes the key so subsequent head() returns None."""
        fake = FakeStorageBackend()
        key = "org/media/del/video.mp4"
        fake.put(key, 2048)
        assert fake.head(key) is not None

        fake.delete(key)
        assert fake.head(key) is None

    def test_delete_absent_key_is_noop(self):
        """delete() on a non-existent key must not raise."""
        fake = FakeStorageBackend()
        fake.delete("never/uploaded")  # should not raise

    def test_presigned_put_url_deterministic_scheme(self):
        """Fake PUT URL starts with the expected fake-storage scheme."""
        fake = FakeStorageBackend()
        url = fake.presigned_put_url("some/key", "video/mp4")
        assert url.startswith("https://fake-storage/put/")

    def test_presigned_get_url_deterministic_scheme(self):
        """Fake GET URL starts with the expected fake-storage scheme."""
        fake = FakeStorageBackend()
        url = fake.presigned_get_url("some/key")
        assert url.startswith("https://fake-storage/get/")


# ---------------------------------------------------------------------------
# S3StorageBackend — structural tests (mocked boto3, no network)
# ---------------------------------------------------------------------------


class TestS3StorageBackendStructural:
    """Structural tests for S3StorageBackend using unittest.mock (no extra deps)."""

    def _make_backend(self):
        """Build an S3StorageBackend with a mocked boto3.client; return (backend, mock_client)."""
        mock_client = MagicMock()
        with patch("boto3.client", return_value=mock_client):
            backend = S3StorageBackend(
                endpoint_url="https://fake.r2.example.com",
                bucket="test-bucket",
                access_key_id="AK",
                secret_access_key="SK",
                region="auto",
                presign_expiry_s=3600,
            )
        return backend, mock_client

    def test_presign_put_calls_generate_presigned_url(self):
        """presigned_put_url delegates to boto3 generate_presigned_url with put_object."""
        backend, mock_client = self._make_backend()
        mock_client.generate_presigned_url.return_value = "https://s3/put-url"

        url = backend.presigned_put_url("org/key.mp4", "video/mp4")

        mock_client.generate_presigned_url.assert_called_once()
        call_args = mock_client.generate_presigned_url.call_args
        assert call_args[0][0] == "put_object"
        assert url == "https://s3/put-url"

    def test_presign_get_calls_generate_presigned_url(self):
        """presigned_get_url delegates to boto3 generate_presigned_url with get_object."""
        backend, mock_client = self._make_backend()
        mock_client.generate_presigned_url.return_value = "https://s3/get-url"

        url = backend.presigned_get_url("org/key.mp4")

        mock_client.generate_presigned_url.assert_called_once()
        call_args = mock_client.generate_presigned_url.call_args
        assert call_args[0][0] == "get_object"
        assert url == "https://s3/get-url"

    def test_put_bytes_calls_put_object_with_content_type(self):
        """put_bytes() uploads server-side via put_object with the given ContentType.

        This is the server-generated-artifact path (rendered slide decks) — no
        presign; the server holds the bytes.
        """
        backend, mock_client = self._make_backend()

        backend.put_bytes("org/decks/x/deck.pdf", b"%PDF-fake", "application/pdf")

        mock_client.put_object.assert_called_once_with(
            Bucket="test-bucket",
            Key="org/decks/x/deck.pdf",
            Body=b"%PDF-fake",
            ContentType="application/pdf",
        )

    def test_head_returns_size_on_success(self):
        """head() returns {"size": ContentLength} when the object exists."""
        backend, mock_client = self._make_backend()
        mock_client.head_object.return_value = {"ContentLength": 4096, "ETag": '"abc"'}

        result = backend.head("org/key.mp4")

        assert result == {"size": 4096}
        mock_client.head_object.assert_called_once_with(Bucket="test-bucket", Key="org/key.mp4")

    def test_head_returns_none_on_404(self):
        """head() returns None when head_object raises a 404 ClientError."""
        import botocore.exceptions

        backend, mock_client = self._make_backend()
        error_response = {"Error": {"Code": "404", "Message": "Not Found"}}
        mock_client.head_object.side_effect = botocore.exceptions.ClientError(
            error_response, "HeadObject"
        )

        result = backend.head("org/missing.mp4")
        assert result is None

    def test_head_returns_none_on_nosuchkey(self):
        """head() returns None when head_object raises a NoSuchKey ClientError."""
        import botocore.exceptions

        backend, mock_client = self._make_backend()
        error_response = {"Error": {"Code": "NoSuchKey", "Message": "No such key"}}
        mock_client.head_object.side_effect = botocore.exceptions.ClientError(
            error_response, "HeadObject"
        )

        result = backend.head("org/gone.mp4")
        assert result is None


# ---------------------------------------------------------------------------
# get_storage provider
# ---------------------------------------------------------------------------


def test_get_storage_raises_when_bucket_not_set(monkeypatch):
    """get_storage() raises HTTPException(503) when STORAGE_BUCKET is empty."""
    from fastapi import HTTPException

    from app.media.storage import get_storage
    from app.config import settings

    monkeypatch.setattr(settings, "STORAGE_BUCKET", "")
    with pytest.raises(HTTPException) as exc_info:
        get_storage()
    assert exc_info.value.status_code == 503


def test_get_storage_returns_s3_when_bucket_set(monkeypatch):
    """get_storage() returns an S3StorageBackend when STORAGE_BUCKET is configured."""
    from app.media.storage import get_storage
    from app.config import settings

    monkeypatch.setattr(settings, "STORAGE_BUCKET", "my-bucket")
    monkeypatch.setattr(settings, "STORAGE_ENDPOINT_URL", "https://fake.r2.example.com")
    monkeypatch.setattr(settings, "STORAGE_ACCESS_KEY_ID", "AK")
    monkeypatch.setattr(settings, "STORAGE_SECRET_ACCESS_KEY", "SK")

    with patch("boto3.client", return_value=MagicMock()):
        result = get_storage()
    assert isinstance(result, S3StorageBackend)
