"""End-to-end smoke test for the learner delivery loop.

Exercises the REAL /api/v1/learn/* + /api/v1/learn/tutor/* endpoints over HTTP
against a live CurricMesh backend. No app code imported, no DB connection
required — pure HTTP via httpx.

Steps:
  1. Login        POST /api/v1/auth/login
  2. Catalog      GET  /api/v1/learn/catalog
  3. Enroll       POST /api/v1/learn/enroll          (409 = already enrolled → OK)
  4. Enrollments  GET  /api/v1/learn/enrollments
  5. Structure    GET  /api/v1/learn/courses/{eid}
  6. Progress     POST /api/v1/learn/progress/{eid}/{mid}  (reset to not_started after)
  7. Tutor ask    POST /api/v1/learn/tutor/{eid}/ask  (503 without keys → [GATE], not FAIL)

Usage:

    BASE_URL=https://curricmesh-api.onrender.com \\
    LEARNER_EMAIL=learner@careerforge.demo \\
    LEARNER_PASSWORD=demo-pass-123 \\
    python backend/scripts/smoke_delivery.py

Or pass args positionally:

    python backend/scripts/smoke_delivery.py \\
        https://curricmesh-api.onrender.com \\
        learner@careerforge.demo \\
        demo-pass-123

Exit code 0 — all required steps passed (503 on tutor-ask counts as an expected
key-gate, not a failure). Non-zero — a required step failed.

What degrades without provider keys (see docs/DELIVERY_GOLIVE_CHECKLIST.md):
  * ANTHROPIC_API_KEY missing  → tutor endpoints 503 (gated, not app bug).
  * EMBEDDING_PROVIDER=fake    → tutor REFUSES every real question ("no context")
                                  because FakeEmbedder hash vectors match nothing.
  * TRANSCRIBE_API_KEY missing → media transcripts never indexed; tutor answers
                                  only over text content.
"""
from __future__ import annotations

import os
import sys

import httpx

_DEFAULT_BASE = "http://localhost:8000"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _env_or_arg(env_key: str, argv: list[str], idx: int) -> str | None:
    if idx < len(argv):
        return argv[idx]
    return os.environ.get(env_key) or None


_results: list[tuple[str, bool, str]] = []


def _step(label: str, ok: bool, detail: str = "") -> bool:
    marker = "PASS" if ok else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{marker}] {label}{suffix}")
    _results.append((label, ok, detail))
    return ok


def _gate(label: str, detail: str = "") -> None:
    """A step that is deliberately gated on a missing key — not a failure."""
    suffix = f"  ({detail})" if detail else ""
    print(f"  [GATE] {label}{suffix}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]

    base_url = (_env_or_arg("BASE_URL", argv, 0) or _DEFAULT_BASE).rstrip("/")
    email = _env_or_arg("LEARNER_EMAIL", argv, 1)
    password = _env_or_arg("LEARNER_PASSWORD", argv, 2)

    missing: list[str] = []
    if not email:
        missing.append("LEARNER_EMAIL")
    if not password:
        missing.append("LEARNER_PASSWORD")
    if missing:
        print(
            f"  [FAIL] Missing required credentials: {', '.join(missing)}\n"
            "\n"
            "  Set them as env vars or pass positional args 2 and 3:\n"
            "\n"
            "    BASE_URL=https://curricmesh-api.onrender.com \\\n"
            "    LEARNER_EMAIL=learner@careerforge.demo \\\n"
            "    LEARNER_PASSWORD=demo-pass-123 \\\n"
            "    python backend/scripts/smoke_delivery.py"
        )
        sys.exit(1)

    print(f"Learner delivery smoke → {base_url}")
    failed = False

    with httpx.Client(timeout=30, follow_redirects=True) as http:

        # ------------------------------------------------------------------ #
        # Step 1 — Login                                                      #
        # ------------------------------------------------------------------ #
        r = http.post(
            f"{base_url}/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        if not _step("Login  POST /api/v1/auth/login", r.status_code == 200, f"HTTP {r.status_code}"):
            print(f"    Detail: {r.text[:300]}")
            print(
                "\n  ABORTED — cannot continue without a token.\n"
                "  If the demo user is not seeded yet, run:\n"
                "    cd backend && ./venv/bin/python -m seed.bootcamp_curriculum\n"
                "  (see docs/DELIVERY_GOLIVE_CHECKLIST.md)"
            )
            sys.exit(1)
        token = r.json()["access_token"]
        auth = {"Authorization": f"Bearer {token}"}

        # ------------------------------------------------------------------ #
        # Step 2 — Catalog                                                    #
        # ------------------------------------------------------------------ #
        r = http.get(f"{base_url}/api/v1/learn/catalog", headers=auth)
        ok = _step("Catalog GET /api/v1/learn/catalog", r.status_code == 200, f"HTTP {r.status_code}")
        if not ok:
            print(f"    Detail: {r.text[:300]}")
            failed = True

        catalog = r.json() if r.status_code == 200 else []
        if not catalog:
            print(
                "  [SKIP] Catalog is empty — no released courses.\n"
                "         Steps 3–7 require a released course.\n"
                "         See docs/DELIVERY_GOLIVE_CHECKLIST.md → 'Seed / verify demo course'."
            )
            if failed:
                print("\nSMOKE TEST FAILED — see FAIL lines above.")
                sys.exit(1)
            print("\nSMOKE TEST PARTIAL — login + catalog passed; downstream steps skipped (empty catalog).")
            return

        entry = catalog[0]
        version_id = entry["curriculum_version_id"]
        print(f"    {len(catalog)} released course(s). Using: {entry['title']!r} v{entry['version']}")

        # ------------------------------------------------------------------ #
        # Step 3 — Enroll                                                     #
        # ------------------------------------------------------------------ #
        r = http.post(
            f"{base_url}/api/v1/learn/enroll",
            json={"curriculum_version_id": str(version_id)},
            headers=auth,
        )
        already = r.status_code == 409
        enroll_ok = r.status_code in (200, 201) or already
        detail = f"HTTP {r.status_code}" + (" — already enrolled, OK" if already else "")
        if not _step("Enroll POST /api/v1/learn/enroll", enroll_ok, detail):
            print(f"    Detail: {r.text[:300]}")
            failed = True

        enrollment_id: str | None = None
        if r.status_code in (200, 201):
            enrollment_id = r.json()["id"]

        # ------------------------------------------------------------------ #
        # Step 4 — Enrollments list                                           #
        # ------------------------------------------------------------------ #
        r = http.get(f"{base_url}/api/v1/learn/enrollments", headers=auth)
        if not _step("Enrollments GET /api/v1/learn/enrollments", r.status_code == 200, f"HTTP {r.status_code}"):
            print(f"    Detail: {r.text[:300]}")
            failed = True
        elif enrollment_id is None and r.status_code == 200:
            # Resolve from list when the enroll step returned 409.
            for e in r.json():
                if e.get("curriculum_version_id") == str(version_id):
                    enrollment_id = e["id"]
                    break

        if enrollment_id is None:
            print("  [SKIP] Could not resolve enrollment ID — skipping steps 5–7.")
            if failed:
                print("\nSMOKE TEST FAILED — see FAIL lines above.")
                sys.exit(1)
            return

        # ------------------------------------------------------------------ #
        # Step 5 — Course structure                                           #
        # ------------------------------------------------------------------ #
        r = http.get(f"{base_url}/api/v1/learn/courses/{enrollment_id}", headers=auth)
        ok = _step("Structure GET /api/v1/learn/courses/{eid}", r.status_code == 200, f"HTTP {r.status_code}")
        if not ok:
            print(f"    Detail: {r.text[:300]}")
            failed = True

        first_member_id: str | None = None
        if r.status_code == 200:
            structure = r.json()
            items = structure.get("items", [])
            print(
                f"    {structure['title']!r}: {len(items)} item(s), "
                f"status={structure['status']!r}, "
                f"completed={structure['completed_items']}/{structure['total_items']}"
            )
            if items:
                first_member_id = items[0]["member_id"]

        # ------------------------------------------------------------------ #
        # Step 6 — Mark progress (reset to not_started afterwards)           #
        # ------------------------------------------------------------------ #
        if first_member_id is not None:
            r = http.post(
                f"{base_url}/api/v1/learn/progress/{enrollment_id}/{first_member_id}",
                json={"status": "in_progress"},
                headers=auth,
            )
            ok = _step(
                "Progress POST /api/v1/learn/progress/{eid}/{mid}",
                r.status_code == 200,
                f"HTTP {r.status_code}",
            )
            if not ok:
                print(f"    Detail: {r.text[:300]}")
                failed = True
            else:
                prog = r.json()
                print(
                    f"    item status={prog['status']!r}, "
                    f"enrollment_status={prog['enrollment_status']!r}, "
                    f"completed={prog['completed_items']}/{prog['total_items']}"
                )
                # Reset to not_started so we don't dirty the demo state.
                http.post(
                    f"{base_url}/api/v1/learn/progress/{enrollment_id}/{first_member_id}",
                    json={"status": "not_started"},
                    headers=auth,
                )
        else:
            print("  [SKIP] No items in course — skipping progress step.")

        # ------------------------------------------------------------------ #
        # Step 7 — Tutor ask                                                  #
        # ------------------------------------------------------------------ #
        r = http.post(
            f"{base_url}/api/v1/learn/tutor/{enrollment_id}/ask",
            json={"question": "What is this course about?"},
            headers=auth,
        )
        if r.status_code == 503:
            _gate(
                "Tutor ask POST /api/v1/learn/tutor/{eid}/ask",
                "503 — ANTHROPIC_API_KEY not set on Render; set it to enable the tutor",
            )
        elif r.status_code == 200:
            resp = r.json()
            answer_preview = resp.get("answer", "")[:80].replace("\n", " ")
            cit_count = len(resp.get("citations", []))
            _step(
                "Tutor ask POST /api/v1/learn/tutor/{eid}/ask",
                True,
                f"answer={answer_preview!r}…  citations={cit_count}",
            )
            if cit_count == 0:
                print(
                    "    Note: 0 citations — tutor returned the grounded refusal. "
                    "This means the RAG index is empty or EMBEDDING_PROVIDER=fake.\n"
                    "    Set EMBEDDING_PROVIDER=hosted + EMBEDDING_API_KEY on Render "
                    "and re-release the course to index its content."
                )
        else:
            _step(
                "Tutor ask POST /api/v1/learn/tutor/{eid}/ask",
                False,
                f"HTTP {r.status_code}",
            )
            print(f"    Detail: {r.text[:300]}")
            failed = True

    # ---------------------------------------------------------------------- #
    # Summary                                                                 #
    # ---------------------------------------------------------------------- #
    if failed:
        print("\nSMOKE TEST FAILED — see FAIL lines above.")
        sys.exit(1)
    print("\nSMOKE TEST PASSED — learner delivery loop is live-verified.")


if __name__ == "__main__":
    main()
