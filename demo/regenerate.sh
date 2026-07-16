#!/bin/zsh
# One-command regeneration of the voiced CurricMesh demo.
#
# PREREQUISITES (do these first — see README.md):
#   1. The frontend must be DEPLOYED at https://curricmesh.vercel.app
#      (if you changed frontend code: `npm run build` in ../frontend, then `zsh demo/deploy.sh`)
#   2. A mergeable CCR must be seeded that matches record.mjs's title filter:
#        python3 demo/seed_ccr.py
#      (one-time, also tidy the demo course once: python3 demo/tidy_items.py)
#   3. ElevenLabs API key at ~/.elevenlabs_key
#
# Then:  zsh demo/regenerate.sh
# Output: ~/Desktop/curricmesh-demo-v5-voiced.mp4
set -e
DEMO="$(cd "$(dirname "$0")" && pwd)"
WORK=/tmp/cm_beats
FFBIN=/Users/davidreed/anaconda3/bin            # machine dep: ffmpeg/ffprobe live here

mkdir -p "$WORK" /tmp/cm_flow_out /tmp/cm_narr5

echo "[stage] copying bookend assets into the scratch dir ($WORK)"
cp "$DEMO/assets/maya_clip.mp4" "$DEMO/assets/card_intro.png" \
   "$DEMO/assets/card_values.png" "$DEMO/assets/card_outro.png" "$WORK/"
[ -f "$HOME/Desktop/demo-music.mp3" ] || cp "$DEMO/assets/demo-music.mp3" "$HOME/Desktop/demo-music.mp3"

echo "[1/4] record  — drives the live app with Playwright -> /tmp/cm_flow_out/flow.webm"
node "$DEMO/record.mjs"

echo "[2/4] assemble — jump-cuts + dips + bookends -> silent cut"
PATH="$FFBIN:$PATH" python3 "$DEMO/assemble.py"

echo "[3/4] narrate  — ElevenLabs TTS, paced per beat -> /tmp/cm_narr5/"
python3 "$DEMO/narrate.py"

echo "[4/4] mux      — voice + ducked music over the cut"
python3 "$DEMO/mux.py"

echo ">> DONE -> ~/Desktop/curricmesh-demo-v5-voiced.mp4"
