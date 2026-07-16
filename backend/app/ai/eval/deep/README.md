# Advisory Semantic-Eval (DeepEval) — NON-CI, on-demand

This is a **Phase 2.5 prototype**: an on-demand, **non-CI** semantic evaluation
of the Phase-2 advisory **free-text** outputs (the andragogy advisor's notes and
the prereq inferer's reasoning). It grades the qualities the deterministic
snapshot eval (`app/ai/eval/run_eval.py`) structurally cannot — *is this advice
actually grounded, actionable, and faithful to the course context?*

## What it grades

Five [GEval](https://docs.confident-ai.com/docs/metrics-llm-evals) metrics +
one DAG metric over each advisory case (`INPUT` = course context,
`ACTUAL_OUTPUT` = advisory text):

| Metric | Type | Question |
| --- | --- | --- |
| `andragogy_groundedness` | GEval | Grounded in Knowles adult-learning principles and tailored to the stated learner? |
| `actionability` | GEval | A concrete change an author could make, not vague/generic? |
| `non_hallucination` | GEval | References only objectives/items present in the context; invents nothing? |
| `conciseness` | GEval | High-signal — no padding, repetition, or filler? |
| `advisory_framing` | GEval | Framed as a suggestion for a human to decide on, never asserting an automatic change? |
| `advisory_dag` | DAG | Decision graph (see below) — gates on hallucination, then actionability. |

`build_metrics(model, *, include_dag=True)` returns the five GEval dims plus
(optionally) the DAG metric. All five GEval dims work OFFLINE with the
`StubJudge`; the DAG metric needs a **live judge** (see below).

### The `advisory_dag` DAG metric (needs a live judge)

Built with deepeval 4.0.5's `deepeval.metrics.dag` API — a small
`DeepAcyclicGraph` of two `BinaryJudgementNode`s with `VerdictNode` leaves,
wrapped as `DAGMetric(name="advisory_dag", dag=..., model=model)`:

```
root  "references ONLY entities in the context (no invented entities)?"
  ├─ false → VerdictNode(score=0)        # hallucination → hard fail
  └─ true  → "concrete, actionable suggestion (not vague)?"
               ├─ false → VerdictNode(score=4)   # grounded but vague
               └─ true  → VerdictNode(score=10)  # grounded AND actionable
```

Each `BinaryJudgementNode` is parsed via deepeval's internal
`BinaryJudgementVerdict` schema (a `verdict` bool field). The OFFLINE `StubJudge`
only satisfies GEval's `Steps`/`ReasonScore` schemas — **not** that verdict
schema — so the DAG metric **raises offline**. The runner wraps the DAG
`measure` in try/except: offline it records the score as `None` / `n/a (needs
live judge)` and continues; the GEval dims and baseline logic still complete.
**In LIVE mode (key set) it runs for real.** This is the version-sensitive part
of the slice — if a future deepeval changes the DAG node/verdict API, only this
metric is affected, never the GEval path.

Fixtures: by default `fixtures.py` serves the hand-written `CASES` (STRONG ones
that are grounded / actionable / faithful, WEAK ones that are vague or
hallucinate a nonexistent module) so a real judge visibly discriminates.
`load_fixtures()` prefers **captured real engine outputs** when present (see
*Capturing real outputs* below).

## Capturing real outputs (`capture.py`)

To grade what the system **actually** produces (not hand-authored stand-ins),
one command snapshots real engine outputs:

```bash
ANTHROPIC_API_KEY=sk-... ./venv/bin/python -m app.ai.eval.deep.capture
```

With a key it builds a few representative `course_context` + `learner_profile`
cases in-code (no DB), calls the REAL engines (`AIClient.advise` and
`AIClient.infer`), and writes the returned free-text (advice note texts; prereq
reasons) to `fixtures/captured_advisory.json` as `{context, output, source,
kind}` records. **Without a key it prints a clear skip message, writes nothing,
and exits 0.** Once captured, `load_fixtures()` automatically prefers that file
(and logs which source it used).

## Regression baseline + gate

The runner supports a regression baseline at `baseline.json`
(`{fixture_id: {metric_name: score}}`):

```bash
# Save the current scores as the new baseline:
./venv/bin/python -m app.ai.eval.deep.run_deep_eval --save-baseline

# Compare a later run to the baseline (prints a REGRESSIONS section):
./venv/bin/python -m app.ai.eval.deep.run_deep_eval

# Treat regressions as a failure (exit 1) — for a manual CI gate later:
./venv/bin/python -m app.ai.eval.deep.run_deep_eval --strict
```

A `(fixture, metric)` is a **regression** when its score drops by more than
`REGRESSION_TOLERANCE = 0.1` vs the baseline. `None` scores (e.g. the DAG metric
offline) and metrics absent from the baseline are skipped, never flagged. The
compare is a pure function `compare_to_baseline(current, baseline, tolerance)`
(unit-tested directly). Env equivalents: `DEEP_EVAL_SAVE_BASELINE=1`,
`DEEP_EVAL_STRICT=1`. **Without `--strict` the runner always exits 0**
(non-blocking signal, same philosophy as `run_eval.py`).

## Running it

```bash
cd backend

# Real scores (needs a key — uses claude-opus-4-8 as the judge):
ANTHROPIC_API_KEY=sk-... ./venv/bin/python -m app.ai.eval.deep.run_deep_eval

# Offline pipeline demo (no key): canned scores, runs the SAME GEval path:
./venv/bin/python -m app.ai.eval.deep.run_deep_eval
```

Either way it prints a `fixture x metric` score table, writes
`last_deep_eval_report.md`, and **always exits 0** (non-blocking signal, not a
gate — same philosophy as `run_eval.py`).

## LIVE vs OFFLINE STUB

Mirrors the harness-wide LIVE-vs-RECORDED-SNAPSHOT design:

- **LIVE (`AnthropicJudge`)** — used automatically when `ANTHROPIC_API_KEY` is
  set. Wraps Anthropic `claude-opus-4-8` as a `DeepEvalBaseLLM`; GEval generates
  evaluation steps then a score+reason. **Only these scores are meaningful.**
- **OFFLINE STUB (`StubJudge`)** — used with no key. A deterministic
  `DeepEvalBaseLLM` that satisfies GEval's two-step schema contract by returning
  constructed schema instances (`Steps`, then `ReasonScore`), so the **real
  GEval code path runs end-to-end offline**. Scores are **canned (constant) and
  not meaningful** — this proves the pipeline, not the model.

### Why the StubJudge works (deepeval 4.0.5)

GEval drives the judge via `generate_with_schema(prompt, schema=...)`. If the
model returns an **instance of the requested schema class**, deepeval consumes
it directly (no JSON re-parsing). `StubJudge.generate` detects the schema
structurally (`steps` field → evaluation steps; `score`/`reason` fields → a
fixed `score=8` with the default `(0, 10)` range → normalized `0.80`) and
returns the instance. This is more robust than matching GEval's exact prompt
text. **Caveat:** this couples the stub to GEval's internal schema *shape*; if a
future deepeval changes those schemas, the stub may need updating (the smoke
test would catch it).

## Why it's separate from the CI eval

CI must stay **offline, deterministic, and dependency-light**. So:

- `deepeval` is an **optional** extra (`pip install -e ".[eval]"`), never in the
  default install.
- The **app never imports** this `deep/` package.
- The tests that touch it (`tests/ai/test_deep_eval_smoke.py`,
  `tests/ai/test_deep_eval_expanded.py`) start with
  `pytest.importorskip("deepeval")`, so they're skipped in CI.

The deterministic snapshot eval gives reproducible, network-free regression
signal; this slice gives richer (but non-deterministic, key-dependent) semantic
signal on free-text quality. Keeping them separate keeps CI green.
