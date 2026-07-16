# Security Policy

## Scope of this policy

CurricMesh is published as an **engineering portfolio and evaluation artifact**, not a production service. It runs against **synthetic data only** — the demo organization, users, curriculum, and the entire SOTA corpus are fabricated. No real curriculum, student records, organizational data, or PII is stored in or distributed with this repository, and there are **no real secrets** in the codebase.

That said, several classes of vulnerability are relevant to this project, and we want to hear about them.

## In scope

We welcome reports about:

- **Authentication and authorization** — JWT handling, the constant-time login path, session/token management, privilege escalation, and any bypass of the six-role RBAC model.
- **Release-gate integrity** — any path that lets a version reach `active` without a passing QA review and two distinct approvals (incl. ≥1 instructor), or that lets an author self-approve, an approver double-approve, or the AI-QA sentinel verdict satisfy the gate.
- **Data exposure paths** — any code path that leaks a curriculum, version, CCR, QA review, or AI finding across user or organization boundaries.
- **Input handling** — SQL injection, prompt injection that bypasses the QA rubric or causes the AI to score around the gate, command injection, SSRF.
- **Concurrency** — any way to defeat the `SELECT FOR UPDATE` + idempotency guard and double-activate a version.
- **Dependency vulnerabilities** — CVEs in dependencies that affect this codebase as deployed.
- **CI / supply-chain** — workflow misconfigurations exploitable via PRs from forks, or lockfile/audit-gate bypasses.

## Out of scope

- Anything requiring a real curriculum or learner dataset (none exists in this repo).
- Theoretical issues without a demonstrated exploit path.
- Self-XSS or social-engineering scenarios requiring user cooperation.
- Automated scanner output without manual verification.
- Issues in third-party services (Anthropic, Slack, hosting providers) — report those to the relevant vendor.

## How to report

**Email:** drdgreed@gmail.com

**Do not open a public GitHub issue for a security vulnerability.**

Please include:

1. A clear description of the vulnerability.
2. The minimum reproduction steps.
3. The impact you believe it has.
4. Any suggested mitigation.

If your finding involves a working exploit, include it as a private gist or attached file rather than a public link.

## Response commitment

- **Acknowledgment** within 5 business days of receipt.
- **Initial assessment** within 14 days.
- **Remediation timeline** communicated after assessment, prioritized by severity.

Please give a reasonable opportunity to address an issue before public disclosure. Reporters are credited in release notes unless they prefer to remain anonymous.

## Secrets and configuration

This repository ships no real credentials. The `ANTHROPIC_API_KEY` and database credentials are read from `backend/.env`, which is **gitignored** — never commit it. `.env.example` documents the expected variables with placeholder values. Run `git ls-files | grep -i secret` before any push to confirm nothing sensitive is staged.

## Production deployment

This repository is not certified for processing real curriculum, student, or employee data. Anyone deploying CurricMesh in a setting that handles real data is responsible for: a data-processing agreement with the LLM provider, a compliant hosting environment, hardened authentication and RBAC, retention and deletion policies, audit-log review, and any regulatory obligations (FERPA, GDPR, or sector-specific rules) applicable to the deployment jurisdiction.
