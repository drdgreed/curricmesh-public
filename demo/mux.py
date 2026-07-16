import json, subprocess, os
FF="/Users/davidreed/anaconda3/bin/ffmpeg"; FP="/Users/davidreed/anaconda3/bin/ffprobe"
VID="/tmp/cm_beats/q_silent_nomusic.mp4"; MUSIC=os.path.expanduser("~/Desktop/demo-music.mp3")
T=json.load(open("/tmp/cm_narr5/timing.json"))
VDUR=float(subprocess.check_output([FP,"-v","error","-show_entries","format=duration","-of","csv=p=0",VID]).strip())

inputs=["-i",VID,"-i",MUSIC]
for seg in T: inputs+=["-i",seg["file"]]

fc=[]; labels=[]
for i,seg in enumerate(T):
    idx=2+i; ms=int(round(seg["start"]*1000))
    fc.append(f"[{idx}:a]aformat=channel_layouts=stereo,adelay={ms}|{ms}[n{i}]")
    labels.append(f"[n{i}]")
N=len(T)
# amix divides by N (4.2.2 has no normalize=0); segments don't overlap, so volume*N restores them.
# *0.9 leaves headroom under the music.
fc.append(f"{''.join(labels)}amix=inputs={N}:duration=longest,volume={N*0.9},apad=whole_dur={VDUR}[narrfull]")
fc.append("[narrfull]asplit=2[narrA][narrB]")
fc.append(f"[1:a]volume=0.10,afade=t=in:d=1.5,afade=t=out:st={VDUR-2.5}:d=2.5[mus]")
fc.append("[mus][narrA]sidechaincompress=threshold=0.02:ratio=8:attack=15:release=350[musd]")
# amix /2 then *2 == straight sum (voice ~0.9 + ducked music ~0.10); alimiter guards peaks
fc.append("[musd][narrB]amix=inputs=2:duration=first,volume=2,alimiter=limit=0.97[aout]")

out=os.path.expanduser("~/Desktop/curricmesh-demo-v5-voiced.mp4")
cmd=[FF,"-y","-loglevel","error",*inputs,"-filter_complex",";".join(fc),
     "-map","0:v","-map","[aout]","-c:v","copy","-c:a","aac","-b:a","192k",
     "-t",str(VDUR),"-movflags","+faststart",out]
subprocess.run(cmd,check=True)
d=float(subprocess.check_output([FP,"-v","error","-show_entries","format=duration","-of","csv=p=0",out]).strip())
print(f"VOICED: {out}  ({d:.1f}s)")
