// Drives the real app through a scenario and saves screenshots plus the
// on-screen boxes of the elements the video will point at.
//
//   node capture.mjs <shots.json> <out-dir>
//
// shots.json:
// {
//   "baseUrl": "http://localhost:5173",
//   "viewport": { "w": 1600, "h": 900 },        // the app window inside the video
//   "scale": 2,                                  // device pixel ratio; 2 keeps zooms sharp
//   "steps": [
//     { "goto": "/tasks" },
//     { "shot": "tasks-empty", "boxes": { "add": "button:has-text('Добавить')", "title": "#title" } },
//     { "click": "button:has-text('Добавить')" },
//     { "fill": ["#title", "Купить молоко"] },
//     { "press": ["#title", "Enter"] },
//     { "waitFor": "text=Купить молоко" },
//     { "wait": 300 },
//     { "eval": "localStorage.clear()" },
//     { "scroll": 600 },                          // or { "scroll": "#section" }
//     { "shot": "tasks-one", "boxes": { "row": "li >> nth=0" } },
//     { "shot": "landing", "fullPage": true },     // tall page; the player can pan over it
//     { "drag": [".card >> nth=0", ".column >> nth=2"], "at": { "x": 40, "y": 420 } },  // drag and drop;
//                                                 // "at" is the drop point inside the target box
//     { "routes": { "**/api/tasks": { "json": [] } } }      // swap fixtures mid-scenario
//   ],
//   "routes": {                                   // optional: answer API calls with fixtures
//     "**/api/**": { "json": [] },                // catch-all first: later patterns win
//     "**/api/tasks": { "json": [{ "id": 1, "title": "Купить молоко" }] },
//     "**/api/me": { "status": 200, "json": { "name": "Анна" } }
//   },
//   "localStorage": { "token": "demo" }           // optional: set before any page script runs
// }
//
// When several patterns match a request, the one registered last answers, so a "routes" step
// overrides earlier fixtures (e.g. the list after "Save" now contains the new item).
// localStorage values that are not strings are stored as JSON.
//
// Writes <out-dir>/<shot>.png and <out-dir>/boxes.json:
//   { "<shot>": { "w": 1600, "h": 900, "boxes": { "<key>": { "x", "y", "w", "h" } } } }
// Box coordinates are CSS pixels of the viewport; the player maps them onto the frame.

import fs from "node:fs";
import path from "node:path";
import { launch } from "./browser.mjs";

const [scenarioPath, outDir] = process.argv.slice(2);
if (!scenarioPath || !outDir) {
  console.error("usage: node capture.mjs <shots.json> <out-dir>");
  process.exit(1);
}
const scenario = JSON.parse(fs.readFileSync(scenarioPath, "utf8"));
const vp = { width: scenario.viewport?.w ?? 1600, height: scenario.viewport?.h ?? 900 };
fs.mkdirSync(outDir, { recursive: true });

const browser = await launch();
const context = await browser.newContext({
  viewport: vp,
  deviceScaleFactor: scenario.scale ?? 2,
  locale: scenario.locale ?? "ru-RU",
  reducedMotion: "reduce",
});
async function addRoutes(routes) {
  for (const [pattern, reply] of Object.entries(routes ?? {}))
    await context.route(pattern, (route) =>
      route.fulfill({
        status: reply.status ?? 200,
        contentType: reply.json !== undefined ? "application/json" : reply.contentType ?? "text/plain",
        body: reply.json !== undefined ? JSON.stringify(reply.json) : reply.body ?? "",
      }),
    );
}
await addRoutes(scenario.routes);
if (scenario.localStorage)
  await context.addInitScript((items) => {
    for (const [k, v] of Object.entries(items))
      localStorage.setItem(k, typeof v === "string" ? v : JSON.stringify(v));
  }, scenario.localStorage);
const page = await context.newPage();
page.setDefaultTimeout(scenario.timeout ?? 15000);

const boxesFile = path.join(outDir, "boxes.json");
const boxes = fs.existsSync(boxesFile) ? JSON.parse(fs.readFileSync(boxesFile, "utf8")) : {};

async function settle() {
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.evaluate(() => document.fonts.ready);
  // Hide the text caret and scrollbars so stills look clean.
  await page.addStyleTag({
    content: "*{caret-color:transparent!important}::-webkit-scrollbar{display:none}",
  });
}

for (const [i, step] of scenario.steps.entries()) {
  const label = `step ${i + 1}: ${JSON.stringify(step)}`;
  try {
    if (step.goto) {
      await page.goto(new URL(step.goto, scenario.baseUrl).href);
      await settle();
    } else if (step.click) await page.click(step.click);
    else if (step.fill) await page.fill(step.fill[0], step.fill[1]);
    else if (step.type) await page.locator(step.type[0]).pressSequentially(step.type[1]);
    else if (step.press) await page.press(step.press[0], step.press[1]);
    else if (step.hover) await page.hover(step.hover);
    else if (step.select) await page.selectOption(step.select[0], step.select[1]);
    else if (step.waitFor) await page.waitForSelector(step.waitFor);
    else if (step.wait) await page.waitForTimeout(step.wait);
    else if (step.eval) await page.evaluate(step.eval);
    else if (step.drag)
      await page.dragAndDrop(step.drag[0], step.drag[1], step.at ? { targetPosition: step.at } : {});
    else if (step.routes) await addRoutes(step.routes);
    else if (step.scroll !== undefined) {
      if (typeof step.scroll === "number") await page.evaluate((y) => window.scrollTo(0, y), step.scroll);
      else await page.locator(step.scroll).scrollIntoViewIfNeeded();
      await page.waitForTimeout(200);
    } else if (step.shot) {
      await settle();
      if (step.fullPage) await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: path.join(outDir, `${step.shot}.png`), fullPage: !!step.fullPage });
      const found = {};
      for (const [key, selector] of Object.entries(step.boxes ?? {})) {
        const box = await page.locator(selector).first().boundingBox();
        if (!box) throw new Error(`box "${key}" (${selector}) is not visible`);
        found[key] = { x: box.x, y: box.y, w: box.width, h: box.height };
      }
      const h = step.fullPage ? await page.evaluate(() => document.documentElement.scrollHeight) : vp.height;
      boxes[step.shot] = { w: vp.width, h, boxes: found };
      console.log(`shot ${step.shot} (${Object.keys(found).length} boxes)`);
    } else throw new Error("unknown step");
  } catch (err) {
    await page.screenshot({ path: path.join(outDir, "_error.png") }).catch(() => {});
    console.error(`${label} failed: ${err.message}\nsee ${path.join(outDir, "_error.png")}`);
    fs.writeFileSync(boxesFile, JSON.stringify(boxes, null, 2));
    await browser.close();
    process.exit(1);
  }
}

fs.writeFileSync(boxesFile, JSON.stringify(boxes, null, 2));
await browser.close();
