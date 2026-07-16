# SOTA Corpus — Synthetic Agentic AI Dataset

**ALL ENTRIES ARE SYNTHETIC / FICTIONAL.** This corpus is a portfolio artifact
created for the CurricMesh demo project. Company names, job postings, product
release notes, and quoted statistics are invented. They do NOT represent real
job listings, real products, or real organizations.

## Purpose

This corpus represents the 2026 "state of the art" in the agentic AI field as
seen from job postings and vendor documentation. It is used by:

- **C2 gap researcher** — LLM-driven analysis that compares bootcamp coverage
  to industry demand signals in this corpus.
- **C4 eval harness** — precision/recall scoring against the ground-truth
  planted-gap labels in `PLANTED_GAPS.json`.

## Contents

| File | Kind | Count |
|------|------|-------|
| `job_postings.json` | `job_posting` | 20 entries |
| `vendor_docs.json` | `vendor_doc` | 20 entries |
| `PLANTED_GAPS.json` | ground-truth manifest | — |

Total: 40 corpus entries.

## Planted Gaps

Three topics appear frequently across corpus entries but are NOT taught (or
only barely mentioned) in the "Agentic AI Architecture in Production" bootcamp.
These are the **planted gaps** — the ground truth for C4 precision/recall eval.

See `PLANTED_GAPS.json` for the machine-readable manifest.
