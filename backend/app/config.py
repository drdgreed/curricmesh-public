from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "postgresql+asyncpg://curricmesh:curricmesh@localhost:5432/curricmesh"
    SECRET_KEY: str = "dev-only-change-me"

    @field_validator("DATABASE_URL")
    @classmethod
    def _ensure_asyncpg_driver(cls, v: str) -> str:
        """Normalize any Postgres URL scheme to the asyncpg driver the app uses.

        Managed providers (Render/Railway/RDS) hand back a plain ``postgres://``
        or ``postgresql://`` connection string — the wrong driver for the async
        engine. Accept whatever form is supplied so DATABASE_URL "just works"
        (AGENT_LESSONS P-007). Idempotent: an already-``+asyncpg`` URL is
        returned unchanged.
        """
        if v.startswith("postgresql+asyncpg://"):
            return v
        for bad in ("postgresql+psycopg2://", "postgresql://", "postgres://"):
            if v.startswith(bad):
                return "postgresql+asyncpg://" + v[len(bad):]
        return v
    ANTHROPIC_API_KEY: str = ""
    # AI model used for every structured-output call. Default is balanced;
    # set AI_MODEL=claude-fable-5 for the most capable model (~2x cost).
    AI_MODEL: str = "claude-opus-4-8"
    CORS_ORIGINS: str = "http://localhost:3000"
    APP_ENV: str = "development"

    # Access-token lifetime (minutes). Configurable so a stricter deployment can
    # dial it down (e.g. ACCESS_TOKEN_MINUTES=30); the default is generous for an
    # exploratory SYNTHETIC-DATA demo (no real users/PII/secrets). Production
    # hardening (HttpOnly cookie + refresh/revocation) is documented in DEMO-FAQ.
    ACCESS_TOKEN_MINUTES: int = 720  # 12h

    # ---------------------------------------------------------------------------
    # Notification settings (B6)
    # ---------------------------------------------------------------------------
    # Slack incoming webhook URL. Leave empty to disable Slack notifications.
    SLACK_WEBHOOK_URL: str = ""

    # SMTP settings for email notifications. Leave SMTP_HOST or NOTIFY_EMAIL_TO
    # empty to disable email notifications.
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    NOTIFY_EMAIL_TO: str = ""
    FROM_EMAIL: str = ""                      # From address (SMTP_USER may be a non-address, e.g. Resend's "resend")
    FRESHNESS_PIPELINE_ENABLED: bool = False  # master gate for the biweekly pipeline runner
    # Judge gate (Phase 2): a gap becomes a CCR only when the AI recommends
    # adopt_now AND its confidence clears this. Below-threshold adopt_now is
    # stored as monitor. David-approved default 0.5 (2026-07-05).
    FRESHNESS_ADOPT_MIN_CONFIDENCE: float = 0.5
    # Phase 3 kill switch: when False (default) adopted CCRs are proposed
    # WITHOUT an executable change_set (Phase-2 shape). Flip after the first
    # supervised generation runs.
    FRESHNESS_GENERATION_ENABLED: bool = False
    # Phase 4 kill switch: when False (default) released curriculum versions are
    # NOT synced to the tenant's GitHub consumer repo. Flip after the first
    # supervised sync runs (seed_sync_target.py → verify manually → enable).
    FRESHNESS_SYNC_ENABLED: bool = False

    # ---------------------------------------------------------------------------
    # Object storage for owned media (S3-compatible; R2 by default).
    # Empty STORAGE_BUCKET disables media (endpoints return 503), mirroring the
    # SMTP-disabled pattern.
    # ---------------------------------------------------------------------------
    STORAGE_ENDPOINT_URL: str = ""       # e.g. https://<acct>.r2.cloudflarestorage.com
    STORAGE_BUCKET: str = ""
    STORAGE_ACCESS_KEY_ID: str = ""
    STORAGE_SECRET_ACCESS_KEY: str = ""
    STORAGE_REGION: str = "auto"         # R2 uses "auto"
    STORAGE_PRESIGN_EXPIRY_S: int = 3600

    # ---------------------------------------------------------------------------
    # Media transcription provider (Whisper-class hosted default).
    # Empty TRANSCRIBE_API_KEY disables transcription (get_transcriber() returns
    # 503), mirroring the STORAGE_BUCKET / SMTP-disabled pattern. No real ASR
    # call is ever made in CI — tests inject FakeTranscriber.
    # ---------------------------------------------------------------------------
    TRANSCRIBE_PROVIDER: str = "openai"          # provider id (hosted default)
    TRANSCRIBE_API_KEY: str = ""                 # set to enable transcription
    TRANSCRIBE_MODEL: str = "whisper-1"          # Whisper-class model id
    TRANSCRIBE_ENDPOINT_URL: str = (             # OpenAI-compatible ASR endpoint
        "https://api.openai.com/v1/audio/transcriptions"
    )
    TRANSCRIBE_TIMEOUT_S: float = 300.0          # hosted call timeout (long audio)

    # ---------------------------------------------------------------------------
    # Live SOTA-research adapter (V2 live field signal)
    # ---------------------------------------------------------------------------
    # When True (and ANTHROPIC_API_KEY is set), a research run can opt into the
    # live web-search corpus provider via ?live=true. Default off -> curated
    # snapshot, so CI/the demo stay deterministic and offline.
    LIVE_SOTA_ENABLED: bool = False
    # Cap the live corpus size (number of web-search docs persisted per run).
    LIVE_SOTA_MAX_RESULTS: int = 20

    # ---------------------------------------------------------------------------
    # External sync adapters (V3-C)
    # ---------------------------------------------------------------------------
    # When False (the default), the GitHub / LMS sync providers run in SIMULATED
    # mode: they format a version manifest and return a deterministic fake URL
    # with ZERO network — which is the demo/CI default. Flipping a flag True is
    # the seam for a future real integration; real network is out of scope for
    # V3-C, so these gate behavior but no HTTP is implemented yet.
    SYNC_GITHUB_ENABLED: bool = False
    SYNC_LMS_ENABLED: bool = False
    # Real-mode credentials (documented seam only — unused while simulated).
    SYNC_GITHUB_TOKEN: str = ""
    SYNC_GITHUB_REPO: str = ""  # e.g. "my-org/my-curriculum"
    SYNC_LMS_BASE_URL: str = ""
    SYNC_LMS_TOKEN: str = ""

    # ---------------------------------------------------------------------------
    # Retrieval / embeddings (Phase B — RAG tutor retrieval infra)
    # ---------------------------------------------------------------------------
    # Provider abstraction with a hosted default (D3). ``fake`` is the CI/dev
    # default: a deterministic in-process embedder so NO real embedding API is
    # ever called in the test suite. Set EMBEDDING_PROVIDER=hosted (+ a provider
    # API key) to use the real governed embedder in a deployment.
    #
    # EMBEDDING_DIM MUST match both the provider model's output width AND the
    # ``Vector(N)`` column width — it is fixed at table-creation time, so
    # changing it requires a migration. 1024 matches a Voyage-class hosted
    # default; the FakeEmbedder emits unit vectors of this width.
    EMBEDDING_PROVIDER: str = "fake"        # fake | hosted
    EMBEDDING_MODEL: str = "voyage-3"       # hosted provider model id
    EMBEDDING_DIM: int = 1024
    EMBEDDING_API_KEY: str = ""             # hosted provider key (unused for fake)
    # Token-bounded chunker: max tokens per chunk + overlap between adjacent
    # chunks (both approximate — see app/core/retrieval/chunker.py).
    RETRIEVAL_CHUNK_TOKENS: int = 400
    RETRIEVAL_CHUNK_OVERLAP: int = 40


settings = Settings()

if settings.APP_ENV == "production" and len(settings.SECRET_KEY) < 32:
    raise RuntimeError("SECRET_KEY must be at least 32 bytes in production")
