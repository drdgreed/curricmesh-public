# Eval Curriculum 2 — Cloud Platform Engineering (SYNTHETIC)

**Everything in this directory is SYNTHETIC / FICTIONAL — a portfolio demo
artifact authored for the CurricMesh AI eval harness.** None of it is scraped
from real job boards, vendors, or curricula.

This is the second eval curriculum (slug `cloud-platform-engineering`), added so
the gap-researcher's precision/recall is measured on more than one program and
gap detection demonstrably generalizes.

## Files

| File | Role |
| --- | --- |
| `covered_topics.json` | ~12 topics the program teaches (Kubernetes, Terraform, CI/CD, GitOps, service mesh basics, cloud cost basics, …). Deliberately EXCLUDES the three planted-gap topics so they are genuinely uncovered. |
| `corpus.json` | 14 synthetic industry docs (`job_posting` + `vendor_doc`). Their bodies collectively mention the three planted gaps at varied frequency, so a LIVE run could genuinely detect them. |
| `planted_gaps.json` | The 3 ground-truth gaps with `canonical_tags` + `signal_strength`, schema-compatible with curriculum 1's `PLANTED_GAPS.json`. |

## Planted gaps (ground truth)

| Topic | Signal strength | Corpus mentions |
| --- | --- | --- |
| eBPF-based Observability | critical | 14/14 (1.0) |
| Platform Engineering & Internal Developer Platforms (IDP) | critical | 14/14 (1.0) |
| FinOps & Cloud Cost Governance | moderate | 8/14 (0.571) |

Mention fractions above are computed from `corpus.json` body text using the same
full-name-OR-canonical-tag matching rule the harness applies when scoring.
