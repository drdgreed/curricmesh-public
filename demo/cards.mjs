import { chromium } from "file:///Users/davidreed/.npm/_npx/e41f203b7505f1fb/node_modules/playwright/index.mjs";

const SIZE = { width: 1280, height: 800 };
const card = (title, sub, foot) => `<!doctype html><html><head><meta charset="utf-8">
<style>
  *{margin:0;box-sizing:border-box;font-family:-apple-system,'Helvetica Neue',Arial,sans-serif}
  body{width:1280px;height:800px;display:flex;align-items:center;justify-content:center;
    background:linear-gradient(150deg,#4f46e5 0%,#3730a3 58%,#1e1b4b 130%);color:#fff;overflow:hidden;position:relative}
  .glow{position:absolute;top:-160px;right:-160px;width:480px;height:480px;border-radius:50%;
    background:rgba(255,255,255,.10);filter:blur(10px)}
  .wrap{position:relative;text-align:center;max-width:900px;padding:0 40px}
  .mark{display:inline-flex;align-items:center;gap:18px;margin-bottom:30px}
  .dia{width:64px;height:64px;border-radius:14px;background:#fff;color:#4f46e5;display:flex;
    align-items:center;justify-content:center;font-size:38px;line-height:1}
  .brand{font-size:46px;font-weight:800;letter-spacing:-.5px}
  h1{font-size:54px;font-weight:800;line-height:1.1;letter-spacing:-1px;margin-bottom:18px}
  p{font-size:26px;opacity:.9;font-weight:500}
  .foot{margin-top:38px;font-size:18px;opacity:.7;letter-spacing:.5px;text-transform:uppercase}
</style></head><body>
  <div class="glow"></div>
  <div class="wrap">
    <div class="mark"><div class="dia">◆</div><div class="brand">CurricMesh</div></div>
    <h1>${title}</h1>
    <p>${sub}</p>
    ${foot ? `<div class="foot">${foot}</div>` : ""}
  </div>
</body></html>`;

const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: SIZE });

await page.setContent(card("Version control for curriculum", "A two-minute guided tour", "for curriculum operations leaders"));
await page.waitForTimeout(300);
await page.screenshot({ path: "/tmp/cm_beats/card_intro.png" });

await page.setContent(card("Curriculum, managed like software.", "Versioned · reviewed · dependency-aware · always current", "curricmesh.vercel.app"));
await page.waitForTimeout(300);
await page.screenshot({ path: "/tmp/cm_beats/card_outro.png" });

await browser.close();
console.log("cards rendered: card_intro.png, card_outro.png");
