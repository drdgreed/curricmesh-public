# Demo Production — Lessons Learned & Rules

What the v5 process taught us, and the rules to follow for the next (longer) cut.
These were earned the hard way: several rounds where work was reported "fixed"
when it wasn't.

## The five rules (non-negotiable)

1. **Verify at the reviewer's exact timestamps — never cherry-pick.** When the
   note is "at 0:43 the buttons are cut off," extract the frame *at 0:43* and
   look. Most false "it's fixed" reports came from checking convenient frames
   (2s, 53s, 108s) instead of the ones complained about. For a longer cut:
   extract frames every ~3–5s across the *whole* video and eyeball each.

2. **Legibility is an acceptance gate, not a nice-to-have.** Text must be both
   large enough and dark enough to read against its background — *read the
   frame*, don't just confirm it's "in frame." MUI's default `text.secondary`
   (`rgba(0,0,0,0.6)`) washes out when scaled; we darkened it to `#374151`.

3. **The entire app must fit the frame on every page** — no clipped buttons,
   panels, or edges, ever.

4. **Use wasted whitespace before shrinking.** If a page is too tall to fit at a
   legible zoom, change the *layout* (wider `maxWidth`, two columns) so it gets
   shorter — then a mild zoom keeps text large. We cut the Propose form 1274→1120px
   by two-columning it, which let zoom rise 0.57→0.67. Never crank zoom down to
   the point text dies.

5. **Every render gets a new filename** (`-v2`, `-v3`, …). Reusing a path leaves
   the reviewer's player showing a *stale cached decode*, so real fixes look like
   no-ops. Tell them exactly which file to open; have them delete the old ones.

## Hard-won technical gotchas

### Deployment (Vercel + Render)
- The frontend reads `import.meta.env.VITE_API_URL` at **build time**. If it's
  not set, the bundle bakes in `localhost:8000` and every API call fails — which
  *looks* like a broken recorder but is a blank-data app. Always build with
  `VITE_API_URL` set (`.env.production`).
- High-level deploy commands (`vercel --prod`, `vercel promote`) did **not**
  re-point the custom domain in this two-project setup. The primitive
  `vercel alias set <deployment> curricmesh.vercel.app` did. When a convenience
  command misbehaves, drop to the primitive.
- The prod API CORS only trusts `curricmesh.vercel.app` — you can't record
  against a preview URL or localhost.
- Render free dynos cold-start (~30–50s). Warm the backend with a login curl
  before recording, or interactive beats time out.

### Recording (Playwright)
- Per-page CSS `zoom` set once after navigation gets **reset on some route
  mounts** → a ~2s full-zoom flash. Fix: a 120ms interval that re-asserts the
  target zoom (`window.__targetZoom`). Use it from the first frame.
- Some views (the Course Builder "New course" landing) use `100vh`-centered
  layouts that **ignore CSS `zoom` entirely** — body *and* html zoom. Don't fight
  it; either avoid showing the view or cut it in the edit (we cut it with a dip).
- Set the zoom *immediately* on nav, not after a settle wait.
- Pick zoom by **measuring** `documentElement.scrollHeight/Width`, not by eye.
- A live approve→merge needs the CCR pre-staged to one approval short, authored
  by a third user (author ≠ both approvers; ≥1 instructor approver).

### Assembly (ffmpeg 4.2.2 on this machine)
- No `xfade` filter → use **dip-to-black** (fade out + fade in) for smooth,
  consistent scene transitions. Keep them identical at every junction.
- `amix` has **no `normalize` option** (added in 4.4) → sum non-overlapping
  tracks with `amix` then `volume=N` to compensate; guard with `alimiter`.
- `-ss/-to` on VFR webm mis-cuts → normalize to CFR (`fps=30`, libx264) first,
  then cut accurately.
- Jump-cut long dead time (the ~18s AI spinner) so the cut stays tight.

### Narration / audio
- ElevenLabs varies run-to-run (tone/volume) → for consistency, regenerate
  uneven lines and/or loudness-normalize each segment.
- Write each line to a word budget for its beat (~2.5 words/sec), generate,
  measure, then `atempo` to fit. Don't let a line overrun into the next action.
- Duck music under voice with `sidechaincompress`, then mix.

## Extra rules for a LONGER version

- **Storyboard first.** For anything past ~2 min, write a beat sheet (timestamp,
  visual, on-screen action, narration line, target duration) *before* recording.
  The video should be paced to the narration, not the reverse.
- **Chapter the recording.** Record long sections as separate clips with clean
  in/out points so a re-take of one chapter doesn't force a full re-record (and
  doesn't burn a fresh CCR seed for unrelated chapters).
- **Lock zoom/legibility per page once**, in a shared config, and reuse — don't
  re-derive per cut.
- **Budget for re-seeds.** Every take that merges consumes the seeded CCR; for a
  multi-take long cut, script the seed so it's one command between takes.
- **Keep a shot log** mapping final-cut timestamps → source clip + flow time, so
  reviewer notes ("fix 4:12") map straight to the right clip and cut point.
- **Pre-clean prod demo data** (or stand up an isolated demo org) so the Review
  list and Course Builder don't show accumulated clutter on camera.
- **Normalize all narration loudness** (EBU R128) across the whole track as a
  final pass, so a 4–5 min cut doesn't drift in level.
