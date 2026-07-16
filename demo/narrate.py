import json, subprocess, os, urllib.request
KEY=open(os.path.expanduser("~/.elevenlabs_key")).read().strip()
VOICE="cCYjmrGZaI86GUJ7F2Nn"
FF="/Users/davidreed/anaconda3/bin/ffmpeg"; FP="/Users/davidreed/anaconda3/bin/ffprobe"
OUT="/tmp/cm_narr5"; os.makedirs(OUT, exist_ok=True)
def dur(f): return float(subprocess.check_output([FP,"-v","error","-show_entries","format=duration","-of","csv=p=0",f]).strip())

# (name, start_in_video, max_seconds_for_this_beat, text)
SEG=[
 ("maya",        4.3,  4.6, "Meet Maya. She runs curriculum operations — and a new agent framework just dropped."),
 ("dashboard",   9.5,  7.0, "Her dashboard tracks every curriculum, its live version, and where content has drifted out of alignment."),
 ("graph",      18.2,  8.4, "The dependency graph shows how each asset supports or requires another — so one change never quietly breaks the next."),
 ("propose",    28.0, 21.0, "To add a new Week 5 lab, Maya composes a structured change-set: she classifies the asset, places it in the calendar, and declares its prerequisites. Then, before anything ships, she asks the AI to analyze its impact across the whole curriculum."),
 ("impact",     53.3,  3.0, "It surfaces the ripple effects, the risks, and the fixes."),
 ("builder",    57.0, 11.5, "In the Course Builder she drafts the lab, and an AI co-pilot reviews it live — flagging gaps in objectives, prerequisites, and the student experience."),
 ("bridge",     69.6,  5.4, "Satisfied, she turns the draft into a change request for review."),
 ("review",     76.4, 15.0, "Nothing goes live on one person's word. The release gate requires passing QA, an instructor's sign-off, and two separate approvals. Maya approves, the gate clears, and she merges — producing a new, immutable version."),
 ("closing",    93.2,  5.6, "The curriculum is updated — fast, governed, and fully traceable."),
 ("value",      99.8,  7.2, "CurricMesh: high-velocity curriculum change, with AI-reinforced quality, human-in-the-loop governance, and complete lineage."),
]

def tts(text, path):
    body=json.dumps({"text":text,"model_id":"eleven_multilingual_v2",
      "voice_settings":{"stability":0.55,"similarity_boost":0.8,"style":0.0,"use_speaker_boost":True,"speed":1.0}}).encode()
    req=urllib.request.Request(f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE}",body,
      {"xi-api-key":KEY,"Content-Type":"application/json","Accept":"audio/mpeg"})
    with urllib.request.urlopen(req) as r, open(path,"wb") as f: f.write(r.read())

timing=[]
for name,start,maxs,text in SEG:
    raw=f"{OUT}/{name}_raw.mp3"; paced=f"{OUT}/{name}.mp3"
    tts(text, raw); d=dur(raw)
    # if it overruns the beat, speed up just enough to fit (atempo<=1.3); else leave natural
    if d>maxs:
        at=min(d/maxs,1.3)
        subprocess.run([FF,"-y","-loglevel","error","-i",raw,"-filter:a",f"atempo={at:.4f}",paced],check=True)
    else:
        subprocess.run([FF,"-y","-loglevel","error","-i",raw,"-c","copy",paced],check=True)
    fd=dur(paced); over = "OVERRUN" if fd>maxs+0.15 else "ok"
    print(f"{name:10s} start {start:5.1f}  raw {d:4.1f}  paced {fd:4.1f}  (budget {maxs})  {over}")
    timing.append({"name":name,"start":start,"dur":round(fd,3),"file":paced})
json.dump(timing, open(f"{OUT}/timing.json","w"), indent=1)
print("\nlast line ends at:", round(timing[-1]["start"]+timing[-1]["dur"],1), "s  (video is 107.5s)")
