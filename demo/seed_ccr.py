import json,urllib.request,urllib.error,time
API="https://curricmesh-api.onrender.com/api/v1"
def req(method,p,b=None,t=None):
    h={"Content-Type":"application/json"}
    if t:h["Authorization"]="Bearer "+t
    r=urllib.request.Request(API+p,json.dumps(b).encode() if b is not None else None,h,method=method)
    try: return json.load(urllib.request.urlopen(r)),200
    except urllib.error.HTTPError as e: return e.read().decode()[:300],e.code
def login(e): return req("POST","/auth/login",{"email":e,"password":"demo-pass-123"})[0]["access_token"]
arch=login("architect@careerforge.demo"); instr=login("instructor@careerforge.demo"); lead=login("instructor_lead@careerforge.demo")
# Look the curriculum up by slug so this survives a prod re-seed (IDs change on reseed).
_curs=req("GET","/curricula",None,arch)[0]
CUR=next((c["id"] for c in _curs if c.get("slug")=="agentic-ai"), _curs[0]["id"])
sfx=str(int(time.time()))
TITLE="Week 5: integrate the new agent framework lab"
key=f"agentic-ai/v1/wk5/lab/eval-framework-{sfx}"
body={"curriculum_id":CUR,"title":TITLE,
  "rationale":"A brand-new agent framework just dropped - add a hands-on Week 5 evaluation lab and ship a minor release.",
  "proposed_bump":"minor","affected_kinds":["lab"],"instructor_override":True,
  "change_set":{"bump":"minor","changed":[],"removed":[],"edges_added":[],"edges_removed":[],
    "added":[{"lineage_key":key,"kind":"lab",
      "content":"# Week 5 Lab: Evaluate the New Agent Framework\n\nInstrument and evaluate the brand-new agent framework end to end with rubric-scored harnesses.",
      "metadata":None,"section":"Week 5: Capstone Labs","week_index":5,"order":0,"source_url":None}]}}
# 1) create (author = instructor_lead, so architect can approve on camera)
d,code=req("POST","/ccrs",body,lead); print("CREATE",code, d if code!=200 else d.get("id"))
if code!=200: raise SystemExit("create failed")
cid=d["id"]
# 2) QA pass by architect (six required dimensions)
dims={"content_accuracy":5,"alignment":5,"prerequisites":4,"consistency":5,"instructor_support":4,"student_experience":5}
d,code=req("POST",f"/ccrs/{cid}/qa",{"dimension_scores":dims,"verdict":"pass"},arch); print("QA(arch)",code, str(d)[:90])
# 3) instructor pre-approval (the instructor approval; architect's on-camera click will be the 2nd)
d,code=req("POST",f"/ccrs/{cid}/approvals",{"decision":"approve"},instr); print("APPROVE(instr)",code, str(d)[:90])
# 4) gate must read: qa_passed True, approval_count 1, has_instructor_approval True, can_release False
g,_=req("GET",f"/ccrs/{cid}/gate",None,arch); print("GATE",g)
print("\nNEW_CCR_ID",cid); print("NEW_TITLE",TITLE)
