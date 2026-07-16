import { chromium } from "file:///Users/davidreed/.npm/_npx/e41f203b7505f1fb/node_modules/playwright/index.mjs";
const SIZE = { width: 1280, height: 800 };
const PROPS = [
  ["High-velocity curriculum change", "update fast, safely"],
  ["AI-reinforced quality checks", "every change is graded"],
  ["Human-in-the-loop governance", "two-approver release gate"],
  ["Dependency-aware consistency", "every ripple is tracked"],
  ["Full lineage &amp; version history", "immutable, auditable, reversible"],
];
const html = `<!doctype html><html><head><meta charset="utf-8"><style>
*{margin:0;box-sizing:border-box;font-family:-apple-system,'Helvetica Neue',Arial,sans-serif}
body{width:1280px;height:800px;background:linear-gradient(150deg,#4f46e5 0%,#3730a3 60%,#1e1b4b 130%);color:#fff;position:relative;overflow:hidden;padding:64px 90px}
.glow{position:absolute;top:-150px;right:-150px;width:460px;height:460px;border-radius:50%;background:rgba(255,255,255,.09);filter:blur(10px)}
.mark{display:flex;align-items:center;gap:14px;margin-bottom:14px}
.dia{width:40px;height:40px;border-radius:10px;background:#fff;color:#4f46e5;display:flex;align-items:center;justify-content:center;font-size:24px}
.brand{font-size:26px;font-weight:800}
h1{font-size:40px;font-weight:800;letter-spacing:-.5px;margin-bottom:28px}
.row{display:flex;align-items:baseline;gap:20px;padding:14px 0;border-top:1px solid rgba(255,255,255,.16)}
.num{font-size:26px;font-weight:800;opacity:.55;min-width:42px}
.txt .t{font-size:25px;font-weight:700}
.txt .s{font-size:17px;opacity:.78;margin-top:2px}
</style></head><body>
<div class="glow"></div>
<div class="mark"><div class="dia">◆</div><div class="brand">CurricMesh</div></div>
<h1>Why curriculum operations leaders choose CurricMesh</h1>
${PROPS.map(([t,s],i)=>`<div class="row"><div class="num">${i+1}</div><div class="txt"><div class="t">${t}</div><div class="s">${s}</div></div></div>`).join("")}
</body></html>`;
const b = await chromium.launch({ channel: "chrome", headless: true });
const p = await b.newPage({ viewport: SIZE });
await p.setContent(html); await p.waitForTimeout(300);
await p.screenshot({ path: "/tmp/cm_beats/card_values.png" });
await b.close(); console.log("value-prop end slide rendered");
