import subprocess, os
OUT="/tmp/cm_beats"; FLOW="/tmp/cm_flow_out/flow.webm"
FF="/Users/davidreed/anaconda3/bin/ffmpeg"; FP="/Users/davidreed/anaconda3/bin/ffprobe"
VENC=["-c:v","libx264","-preset","medium","-crf","20","-pix_fmt","yuv420p","-r","30"]
F=0.45  # standard dip-to-black half-duration
def run(a): subprocess.run(a,check=True)
def dur(f): return float(subprocess.check_output([FP,"-v","error","-show_entries","format=duration","-of","csv=p=0",f]).strip())

# 0) normalize VFR webm -> CFR mp4 (idempotent; already built but safe to redo)
run([FF,"-y","-loglevel","error","-i",FLOW,"-vf","scale=1280:800,fps=30",*VENC,"-an",f"{OUT}/flow_norm.mp4"])
print("flow_norm:", round(dur(f"{OUT}/flow_norm.mp4"),1),"s")

# CUTS:
#   partA [2,46]   dashboard -> graph (zoomed-in labels) -> propose form (0.57) -> analyze -> spinner.  fade IN, hard-cut out.
#   partB [63.5,67] AI impact analysis results (0.57, full panel).  hard-cut in, dip OUT.
#   partC [73,115.5] course-builder DRAFT (0.62) -> item add -> co-pilot -> review (0.54, approve+merge) -> dashboard.  dip IN, dip OUT.
#   The empty "New course" landing (67-73, zoom-resistant) is CUT; the partB->partC dip covers the Propose->Builder scene change.
run([FF,"-y","-loglevel","error","-ss","2.0","-to","46.0","-i",f"{OUT}/flow_norm.mp4",
     "-vf",f"fade=t=in:st=0:d={F}:c=black",*VENC,"-an",f"{OUT}/partA.mp4"])            # 44.0s
run([FF,"-y","-loglevel","error","-ss","65.0","-to","68.5","-i",f"{OUT}/flow_norm.mp4",
     "-vf",f"fade=t=out:st={3.5-F}:d={F}:c=black",*VENC,"-an",f"{OUT}/partB.mp4"])       # 3.5s, dip out
run([FF,"-y","-loglevel","error","-ss","77.0","-to","120.0","-i",f"{OUT}/flow_norm.mp4",
     "-vf",f"fade=t=in:st=0:d={F}:c=black,fade=t=out:st={43.0-F}:d={F}:c=black",*VENC,"-an",f"{OUT}/partC.mp4"])  # 43.0s, dip in+out

# TITLE card (4s): fade in 0.5, dip out 0.45
run([FF,"-y","-loglevel","error","-loop","1","-t","4","-i",f"{OUT}/card_intro.png",
     "-vf",f"scale=1280:800,fps=30,fade=t=in:st=0:d=0.5:c=black,fade=t=out:st={4-F}:d={F}:c=black",*VENC,"-an",f"{OUT}/q_title.mp4"])
# MAYA (5s): dip in/out
run([FF,"-y","-loglevel","error","-i",f"{OUT}/maya_clip.mp4","-t","5",
     "-vf",f"scale=1280:800,fps=30,fade=t=in:st=0:d={F}:c=black,fade=t=out:st={5-F}:d={F}:c=black",*VENC,"-an",f"{OUT}/q_maya.mp4"])
# VALUE slide (8s): dip in, fade out 0.6 at end
run([FF,"-y","-loglevel","error","-loop","1","-t","8","-i",f"{OUT}/card_values.png",
     "-vf",f"scale=1280:800,fps=30,fade=t=in:st=0:d={F}:c=black,fade=t=out:st={8-0.6}:d=0.6:c=black",*VENC,"-an",f"{OUT}/q_values.mp4"])

# concat (dips live inside each clip)
segs=[f"{OUT}/q_title.mp4",f"{OUT}/q_maya.mp4",f"{OUT}/partA.mp4",f"{OUT}/partB.mp4",f"{OUT}/partC.mp4",f"{OUT}/q_values.mp4"]
with open(f"{OUT}/silent.txt","w") as f:
    for s in segs: f.write(f"file '{s}'\n")
run([FF,"-y","-loglevel","error","-f","concat","-safe","0","-i",f"{OUT}/silent.txt","-c","copy",f"{OUT}/q_silent_nomusic.mp4"])
td=dur(f"{OUT}/q_silent_nomusic.mp4"); print(f"silent total: {td:.1f}s ({int(td//60)}:{int(td%60):02d})")

# music bed
out=os.path.expanduser("~/Desktop/curricmesh-demo-v5.mp4")
run([FF,"-y","-loglevel","error","-i",f"{OUT}/q_silent_nomusic.mp4","-i",os.path.expanduser("~/Desktop/demo-music.mp3"),
     "-filter_complex",f"[1:a]volume=0.16,afade=t=in:d=1.5,afade=t=out:st={td-2.5}:d=2.5[a]",
     "-map","0:v","-map","[a]","-c:v","copy","-c:a","aac","-b:a","192k","-t",str(td),"-movflags","+faststart",out])
print(f"DRAFT: {out}  ({dur(out):.1f}s)")
