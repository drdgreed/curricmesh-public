"""DeepEval custom judge models for the advisory-output semantic eval.

Two ``DeepEvalBaseLLM`` implementations, mirroring the harness-wide
LIVE-vs-RECORDED-SNAPSHOT philosophy:

  - ``AnthropicJudge`` — wraps Anthropic ``claude-opus-4-8`` and is used when a
    real ``ANTHROPIC_API_KEY`` is present (LIVE scoring).
  - ``StubJudge`` — a deterministic, offline judge so the SAME GEval code path
    runs with NO key (OFFLINE STUB; scores are canned, not meaningful).

GEval contract (deepeval 4.0.5), discovered by inspecting the package:
  GEval drives the model in two steps via ``generate_with_schema(prompt,
  schema=...)`` (the base class forwards ``schema=`` into ``generate`` /
  ``a_generate``):
    1. evaluation STEPS  -> expects a ``deepeval.metrics.g_eval.schema.Steps``
       instance (``{"steps": [...]}``).
    2. a SCORE + reason  -> expects a ``ReasonScore`` instance
       (``{"score": float, "reason": str}``).
  If ``generate_with_schema`` returns an INSTANCE of the requested schema class,
  deepeval consumes it directly (no JSON re-parsing) — so we return the schema
  instance and avoid all string-parsing brittleness. With no rubric the GEval
  score range is (0, 10), so a raw score of 8 normalizes to 0.8.
"""

from __future__ import annotations

from typing import Any

from deepeval.models import DeepEvalBaseLLM

from app.config import settings

_MODEL = "claude-opus-4-8"
_MAX_TOKENS = 4000


def _schema_instance(schema: type | None, *, score: float = 8.0):
    """Build an instance of whatever GEval schema was requested, or None.

    Detects the two GEval schemas structurally (``steps`` vs ``score``/``reason``
    fields) so we don't hard-depend on deepeval's internal module path.
    """
    if schema is None:
        return None
    fields = getattr(schema, "model_fields", {})
    if "steps" in fields:
        return schema(
            steps=[
                "Read the course context and the andragogy advice.",
                "Check the advice against the stated criteria.",
                "Assign a score reflecting how well the criteria are met.",
            ]
        )
    if "score" in fields and "reason" in fields:
        return schema(score=score, reason="stub offline judge (canned score)")
    return None


class StubJudge(DeepEvalBaseLLM):
    """Deterministic offline judge so the GEval pipeline runs with NO API key.

    Satisfies GEval's two-step schema contract by returning constructed schema
    instances. Scores are CANNED (constant) — this exercises the real pipeline
    end to end, but the numbers are not meaningful. Use the live AnthropicJudge
    for real scores.
    """

    def __init__(self, score: float = 8.0) -> None:
        self._score = score
        super().__init__(model="stub-judge")

    def load_model(self):  # noqa: D401 - required by base class
        return self

    def generate(self, prompt: str, *args: Any, schema: type | None = None, **kwargs: Any):
        inst = _schema_instance(schema, score=self._score)
        if inst is not None:
            return inst
        # No schema requested: return parseable canned JSON so GEval's JSON
        # fallback path can still extract a score/reason if it ever hits it.
        return '{"score": %s, "reason": "stub offline judge"}' % self._score

    async def a_generate(
        self, prompt: str, *args: Any, schema: type | None = None, **kwargs: Any
    ):
        return self.generate(prompt, *args, schema=schema, **kwargs)

    def get_model_name(self) -> str:
        return "StubJudge (offline, canned scores)"


class AnthropicJudge(DeepEvalBaseLLM):
    """DeepEval custom model wrapping Anthropic ``claude-opus-4-8`` for LIVE scoring.

    Tolerates GEval's ``schema=`` kwarg: when a pydantic schema is supplied we
    request structured output (``messages.parse``) and return the parsed
    instance; otherwise we return plain text. Requires ``ANTHROPIC_API_KEY``.
    """

    def __init__(self, model: str = _MODEL) -> None:
        self._model = model
        self._sync_client = None
        self._async_client = None
        super().__init__(model=model)

    def load_model(self):  # noqa: D401 - required by base class
        return self

    @property
    def sync_client(self):
        if self._sync_client is None:
            import anthropic

            self._sync_client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        return self._sync_client

    @property
    def async_client(self):
        if self._async_client is None:
            import anthropic

            self._async_client = anthropic.AsyncAnthropic(
                api_key=settings.ANTHROPIC_API_KEY
            )
        return self._async_client

    def generate(self, prompt: str, *args: Any, schema: type | None = None, **kwargs: Any):
        if schema is not None:
            resp = self.sync_client.messages.parse(
                model=self._model,
                max_tokens=_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
                output_format=schema,
            )
            return resp.parsed_output
        resp = self.sync_client.messages.create(
            model=self._model,
            max_tokens=_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        return _first_text(resp)

    async def a_generate(
        self, prompt: str, *args: Any, schema: type | None = None, **kwargs: Any
    ):
        if schema is not None:
            resp = await self.async_client.messages.parse(
                model=self._model,
                max_tokens=_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
                output_format=schema,
            )
            return resp.parsed_output
        resp = await self.async_client.messages.create(
            model=self._model,
            max_tokens=_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        return _first_text(resp)

    def get_model_name(self) -> str:
        return f"AnthropicJudge ({self._model})"


def _first_text(resp: Any) -> str:
    """Return the first text block from an Anthropic messages response."""
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", None) == "text":
            return block.text
    return ""
