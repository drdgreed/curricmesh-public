import { chromium } from "file:///Users/davidreed/.npm/_npx/e41f203b7505f1fb/node_modules/playwright/index.mjs";
import { renameSync, mkdirSync, rmSync } from "node:fs";
const APP="https://curricmesh.vercel.app", API="https://curricmesh-api.onrender.com/api/v1";
const SIZE={width:1280,height:800};
const lr=await fetch(API+"/auth/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({email:"architect@careerforge.demo",password:"demo-pass-123"})});
const {access_token}=await lr.json(); const me=await (await fetch(API+"/auth/me",{headers:{Authorization:`Bearer ${access_token}`}})).json();
const browser=await chromium.launch({channel:"chrome",headless:true});
const ctx=await browser.newContext({viewport:SIZE,recordVideo:{dir:"/tmp/cm_flow",size:SIZE}});
// auth BEFORE first paint (no /login) + visible cursor — runs on every navigation
await ctx.addInitScript(({t,role,org,orgName})=>{
  try{localStorage.setItem("auth_token",t);localStorage.setItem("auth_role",role||"");localStorage.setItem("auth_org",org||"");localStorage.setItem("auth_org_name",orgName||"");}catch(e){}
  // continuous zoom enforcer: re-assert the target body zoom every 120ms so nothing (route change, remount) can reset it for more than one frame
  try{window.__targetZoom=1;setInterval(()=>{try{const z=String(window.__targetZoom||1);if(document.body&&document.body.style.zoom!==z)document.body.style.zoom=z;}catch(e){}},120);}catch(e){}
  const add=()=>{if(document.getElementById("__cur")||!document.body)return;const c=document.createElement("div");c.id="__cur";c.style.cssText="position:fixed;z-index:2147483647;width:20px;height:20px;border-radius:50%;background:rgba(79,70,229,.30);border:2px solid #4f46e5;box-shadow:0 0 10px rgba(79,70,229,.6);pointer-events:none;transform:translate(-50%,-50%);left:-100px;top:-100px";document.body.appendChild(c);};
  document.addEventListener("DOMContentLoaded",add);window.addEventListener("load",add);
  window.addEventListener("mousemove",e=>{const c=document.getElementById("__cur");if(c){c.style.left=e.clientX+"px";c.style.top=e.clientY+"px";}},true);
},{t:access_token,role:me.role,org:me.org,orgName:me.org_name});
const page=await ctx.newPage();
const t0=Date.now(); const ts=(l)=>console.log(`  @${((Date.now()-t0)/1000).toFixed(1)}s  ${l}`);
async function glide(loc){try{const b=await loc.boundingBox({timeout:8000});if(b){await page.mouse.move(b.x+b.width/2,b.y+b.height/2,{steps:30});await page.waitForTimeout(450);}}catch{}}
async function navTo(label,zoom=1){const l=page.getByText(label,{exact:true}).first();await glide(l);await l.click({timeout:9000});await page.evaluate((z)=>{window.__targetZoom=z;window.scrollTo(0,0);},zoom);await page.waitForTimeout(2000);await page.evaluate((z)=>{window.__targetZoom=z;window.scrollTo(0,0);},zoom);ts("nav "+label);}
async function typeIn(loc,text,delay=45){try{const inner=loc.locator("input,textarea");const tgt=(await inner.count())?inner.first():loc;await glide(tgt);await tgt.click({timeout:7000});await tgt.fill("");await tgt.type(text,{delay});}catch(e){console.log("type warn",e.message.split("\n")[0]);}}
async function pick(testid,re){const s=page.getByTestId(testid);await glide(s);await s.click({timeout:7000});await page.waitForTimeout(1300);if(re){await page.getByRole("option",{name:re}).first().click({timeout:5000}).catch(async()=>{await page.getByRole("option").nth(1).click({timeout:4000});});}else{await page.getByRole("option").first().click({timeout:5000});}await page.waitForTimeout(700);}

await page.goto(APP+"/",{waitUntil:"networkidle"}); await page.waitForTimeout(2500); ts("DASHBOARD");
await page.waitForTimeout(4500);                                            // beat1 dashboard hold
await navTo("Dependency Graph",1); await page.waitForTimeout(2500);
const zin=page.locator(".react-flow__controls-zoomin");                           // zoom the graph IN so edge labels are large/legible
for(let i=0;i<5;i++){await zin.click({timeout:4000}).catch(()=>{});await page.waitForTimeout(380);}
await page.waitForTimeout(3800); ts("GRAPH");
await navTo("Propose Change",0.67); await page.waitForTimeout(1500);  // 2-col layout -> form 1120px -> 0.67: larger+darker text, action bar still fits
try{
  const cur=page.getByTestId("curriculum-select"); if(await cur.count()){await pick("curriculum-select");}
  await typeIn(page.getByTestId("add-lineage-key"),"agentic-ai/v1/wk5/lab/eval-framework",50); await page.waitForTimeout(900);
  await pick("add-kind",/lab/i); await page.waitForTimeout(600);
  await typeIn(page.getByTestId("add-content"),"Hands-on lab: instrument and evaluate the new agent framework end to end.",38); await page.waitForTimeout(700);
  await pick("placement-existing");
  const stage=page.getByTestId("stage-add-btn"); await glide(stage); await stage.click({timeout:9000}); await page.waitForTimeout(1500); ts("STAGED");
  const an=page.getByTestId("analyze-impact"); await glide(an); await page.waitForTimeout(700); await an.click({timeout:8000}); ts("ANALYZE_CLICK");
  try{await page.waitForSelector('[data-testid="impact-objectives"]',{timeout:35000});}catch{} ts("IMPACT_RESULT");
  try{await page.getByTestId("impact-panel").scrollIntoViewIfNeeded({timeout:3000});}catch{} await page.waitForTimeout(4000); ts("impact shown");
}catch(e){console.log("beat3 err",e.message.split("\n")[0]);}
await navTo("Course Builder",0.62); await page.waitForTimeout(1000);  // measured: 1597px wide -> 0.62 fits the right side with margin
try{
  await pick("open-draft-select",/Production-Ready Agentic Systems/);
  await page.waitForSelector('[data-testid="copilot-panel"]',{timeout:15000});
  await page.evaluate(()=>{window.__targetZoom=0.62;window.scrollTo(0,0);}); await page.waitForTimeout(1200); ts("BUILDER");
  await typeIn(page.getByTestId("item-title"),"Capstone: evaluate the new agent framework",55); await page.waitForTimeout(700);
  await typeIn(page.getByTestId("item-content"),"Add evaluation harnesses and cost and latency observability, then run a production readiness review.",35); await page.waitForTimeout(800);
  const ai2=page.getByTestId("add-item-btn"); await glide(ai2); await ai2.click({timeout:5000}).catch(()=>{}); await page.waitForTimeout(1200);
  const adv=page.getByTestId("copilot-advise-btn"); await glide(adv); await page.waitForTimeout(600); await adv.click({timeout:6000}); ts("ADVISE_CLICK");
  try{await page.waitForFunction(()=>document.querySelectorAll('[data-testid="advisor-note"]').length>=2,{timeout:18000});}catch{} ts("NOTES_SHOWN");
  await page.evaluate(()=>window.scrollTo(0,0)); await page.waitForTimeout(3500); ts("copilot shown");
  // keep 0.66 into Review (no flash back to full zoom)
}catch(e){console.log("beat4 err",e.message.split("\n")[0]);}
await navTo("Review",0.54); await page.waitForTimeout(1500);  // measured: expanded CCR 1348px tall -> 0.54 fits approve/merge with margin
try{
  await page.waitForSelector('[data-testid="review-list"]',{timeout:10000});
  const item=page.getByTestId("review-item").filter({hasText:"Week 5: integrate the new agent framework lab"}).first(); await item.scrollIntoViewIfNeeded({timeout:6000}); await glide(item); await item.click({timeout:8000}); await page.waitForTimeout(800);
  await item.evaluate(el=>el.scrollIntoView({block:"start"})); await page.waitForTimeout(1000);   // pin the focused CCR to the top so clutter rows (incl. ZZZ) scroll off-screen
  const appr=page.getByTestId("approve-btn"); await glide(appr); await page.waitForTimeout(600); await appr.click({timeout:8000}); ts("APPROVE_CLICK");
  await page.waitForSelector('[data-testid="approve-success"]',{timeout:15000}); await page.waitForTimeout(2300); ts("gate flipped");
  const mg=page.getByTestId("merge-btn"); await glide(mg); await page.waitForTimeout(600); await mg.click({timeout:12000}); ts("MERGE_CLICK");
  await page.waitForSelector('[data-testid="merge-success"]',{timeout:35000}); ts("MERGED");
  try{await item.evaluate(el=>el.scrollIntoView({block:"start"}));}catch{} await page.waitForTimeout(3500); ts("merge shown");  // keep the focused CCR (now showing the merge result) pinned to the top
}catch(e){console.log("beat5 err",e.message.split("\n")[0]);}
await navTo("Dashboard",1); await page.waitForTimeout(2000);  // reset to full zoom for closing dashboard
try{await page.getByTestId("ai-spend-tile").scrollIntoViewIfNeeded({timeout:4000});}catch{} await page.waitForTimeout(4500); ts("DASHBOARD_END");
const v=page.video(); await ctx.close(); const f=await v.path(); rmSync("/tmp/cm_flow_out",{recursive:true,force:true}); mkdirSync("/tmp/cm_flow_out",{recursive:true}); renameSync(f,"/tmp/cm_flow_out/flow.webm");
await browser.close(); console.log("FLOW DONE -> /tmp/cm_flow_out/flow.webm");
