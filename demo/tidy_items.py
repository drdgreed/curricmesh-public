import json,urllib.request,urllib.error
API="https://curricmesh-api.onrender.com/api/v1"
def req(method,p,b=None,t=None):
    h={"Content-Type":"application/json"}
    if t:h["Authorization"]="Bearer "+t
    r=urllib.request.Request(API+p,json.dumps(b).encode() if b is not None else None,h,method=method)
    try: return json.load(urllib.request.urlopen(r)),200
    except urllib.error.HTTPError as e: return e.read().decode()[:200],e.code
tok=req("POST","/auth/login",{"email":"architect@careerforge.demo","password":"demo-pass-123"})[0]["access_token"]

# Rewrite the 10 duplicate items into a realistic 8-week agentic-AI outline (PATCH; no delete endpoint exists).
# (id, title, kind, week, order, content)
plan=[
 ("922682f9-8508-4710-bc2f-ff53a2af4752","Foundations of Agentic Systems","lesson_plan",1,0,"Agent loops, planning, and tool use — the core building blocks."),
 ("e0fbb609-2a44-4662-a871-f15fa6ca3999","Tool-Calling & Function Routing Lab","lab",2,0,"Hands-on: wire tools to an agent and route calls reliably."),
 ("4f784b30-378f-44ca-981c-81054cdea62a","Memory & Long-Context Management","lesson_plan",3,0,"Strategies for memory, retrieval, and long-context budgeting."),
 ("16dfd15a-e0e3-490f-a9e9-9952c2738e6f","Multi-Agent Orchestration Lab","lab",4,0,"Coordinate multiple agents with shared state and handoffs."),
 ("231e7cb2-623d-4d9b-b4c0-c8956bdb77ff","Evaluation & Observability Lab","lab",5,0,"Instrument rubric-scored evals plus cost/latency observability."),
 ("1a7044d7-ceb1-491d-85bb-c7eda26750fb","Reading: Evaluation Harness Patterns","references",5,1,"Curated readings on building robust evaluation harnesses."),
 ("44835529-35a1-4159-b755-3a3a9f47da11","Safety, Alignment & Guardrails","lesson_plan",6,0,"Guardrails, alignment checks, and failure-mode analysis."),
 ("b0d9fb63-9a2a-4042-90e0-eeccc618d267","Red-Teaming Agentic Systems","lab",7,0,"Adversarially probe an agent for unsafe or brittle behavior."),
 ("4b2f98e6-d87b-4f77-93c5-23c89bf255f4","Capstone: Production Readiness Review","assessment",8,0,"Assess an agent system against a production-readiness rubric."),
 ("c5d6041f-1bc3-47c4-8626-35186aeba408","Capstone Lab: Deploy & Monitor an Agent","lab",8,1,"Ship an agent to a staging environment and monitor it live."),
]
ok=0
for iid,title,kind,wk,order,content in plan:
    body={"title":title,"kind":kind,"week_index":wk,"order_index":order,"content":content}
    d,code=req("PATCH",f"/builder/items/{iid}",body,tok)
    print(("OK  " if code==200 else f"ERR {code} ")+f"w{wk} {title}"+("" if code==200 else f"  -> {d}"))
    ok+= (code==200)
print(f"\n{ok}/10 items tidied")
