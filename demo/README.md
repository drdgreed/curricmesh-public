# CurricMesh Demo Video Pipeline

Reproducible pipeline that produces the ~1:47 voiced product demo
(`curricmesh-demo-v5-voiced.mp4`). It drives the **live deployed app** with
Playwright, edits the recording, generates narration with ElevenLabs, and mixes
voice + music.

> **Current state:** v5 voiced cut is the **approved beta** (2026-06-13), pending
> human-reviewer notes. Known issue: mild voice tone/volume variance between lines.

## TL;DR — regenerate the whole video

```bash
# 0. (only if frontend code changed) commit + push — the `curricmesh` Vercel
#    project is git-connected and auto-deploys `main` to https://curricmesh.vercel.app
git add -A && git commit -m "..." && git push

# 1. seed a fresh mergeable CCR (prod write) + one-time course tidy
python3 demo/seed_ccr.py
python3 demo/tidy_items.py        # only needed once, or after the course gets messy

# 2. render the whole video (record -> assemble -> narrate -> mux)
zsh demo/regenerate.sh
# -> ~/Desktop/curricmesh-demo-v5-voiced.mp4
```

## The pipeline (4 stages)

| Stage | Script | What it does | Output |
|---|---|---|---|
| Record | `record.mjs` | Headless Playwright (system Chrome) logs in via injected token, drives Dashboard → Graph → Propose → Course Builder → Review (live approve+merge) → Dashboard. Per-page **fit-zoom** enforced by a 120ms interval; graph zoomed IN for legible edge labels. | `/tmp/cm_flow_out/flow.webm` |
| Assemble | `assemble.py` | Normalizes VFR→CFR, jump-cuts the AI spinner, **cuts the zoom-resistant empty "New course" form** with a dip, adds title + Maya + value-slide bookends, dip-to-black at every junction. | `~/Desktop/curricmesh-demo-v5.mp4` (+ `/tmp/cm_beats/q_silent_nomusic.mp4`) |
| Narrate | `narrate.py` | ElevenLabs TTS (voice `cCYjmrGZaI86GUJ7F2Nn`, "David – Deep, Warm"). 10 lines, each **paced (atempo) to fit its beat**. | `/tmp/cm_narr5/*.mp3` + `timing.json` |
| Mux | `mux.py` | Places each line at its beat start, **ducks music under voice** (sidechain), limits peaks. | `~/Desktop/curricmesh-demo-v5-voiced.mp4` |

### Prod-data prerequisites (run before recording)

- `seed_ccr.py` — creates a CCR titled **"Week 5: integrate the new agent framework lab"** in the exact pre-merge state the recorder needs: passing QA + **one** instructor approval, so the architect's single on-camera click crosses the release gate (`approval_count 1 → 2`). The recorder merges it, so **re-seed before every record**. If you change the title, update the `filter({hasText: ...})` in `record.mjs` to match.
- `tidy_items.py` — rewrites the demo course's duplicate items into a clean 8-week outline (the Course Builder has no item-delete API).

### Card / asset generators

- `cards.mjs` → `card_intro.png`, `card_outro.png`
- `endslide.mjs` → `card_values.png` (the 5 value props)
- `assets/maya_clip.mp4` — built from `assets/Maya.jpg` (blurred-fill portrait). `assets/demo-music.mp3` — background bed.

## Constants you may need to change

- **App / API:** `https://curricmesh.vercel.app` and `https://curricmesh-api.onrender.com/api/v1` (top of each script).
- **Demo creds:** `architect@careerforge.demo` / `demo-pass-123` (plus `instructor@` and `instructor_lead@` for seeding).
- **Voice ID:** `cCYjmrGZaI86GUJ7F2Nn` (in `narrate.py`).
- **Per-page zoom (record.mjs):** Propose `0.67`, Course Builder `0.62`, Review `0.54`, graph = 5× zoom-in clicks. These come from measuring each page's pixel height; re-measure if the UI changes.

## Machine dependencies (this is not yet fully portable)

- ffmpeg/ffprobe at `/Users/davidreed/anaconda3/bin` (no `xfade` — it's 4.2.2, hence dip-to-black not crossfades; `amix` has no `normalize`, hence volume-compensation in `mux.py`).
- Playwright imported by absolute path from an `npx` cache in `record.mjs` — update if it moves.
- Deploys are **git-driven**: push to `main` → the `curricmesh` Vercel project auto-builds and updates `https://curricmesh.vercel.app`. (The old `frontend` project + manual `vercel alias` dance has been retired; `seed_ccr.py` looks the curriculum up by slug so it survives a re-seed.) Backend kept warm by `.github/workflows/keep-warm.yml`.
- ElevenLabs key at `~/.elevenlabs_key` (never commit it).

## Known limitations

- **Review-list clutter:** old CCRs (incl. a "ZZZ" test) show as faint rows above the focused one. No CCR delete/edit API; the list fits on screen so nothing scrolls them off. Needs a backend delete endpoint or a DB cleanup.
- **Voice tone/volume variance** between lines (ElevenLabs run-to-run). Fix by regenerating uneven lines, or normalize loudness in `mux.py`.

See `LESSONS.md` for what this process taught us and the rules for a longer cut.
